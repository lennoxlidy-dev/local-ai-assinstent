import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../common"

// Left: the routines. Right: the editor (name, description, steps). "Say something", "Play a sound" and "Wait" have
// their own fields; other actions take their details as JSON.
Item {
    id: tab
    objectName: "routinesTab"
    readonly property var tr: settings.tr
    readonly property var th: look.colors
    property int current: -1
    property string name: ""
    property string old: ""
    property string desc: ""
    property var steps: []
    property bool dirty: false
    property string saveError: ""
    // Every change saves itself shortly after the last edit (like all other settings) - before, only the Save button
    // at the very bottom stored it, and edits such as "at least … s" were lost unnoticed.
    Timer { id: autosave; interval: 700; onTriggered: tab.save(true) }
    function touched() { dirty = true; autosave.restart() }
    function flush() { autosave.stop(); if (dirty) save(true) }
    Component.onDestruction: { try { flush() } catch (e) {} }      // tab switched / window closed
    function load(i) {
        flush()
        var r = settings.routines[i]
        current = i; old = r.name; name = r.name; desc = r.description || ""
        steps = r.steps.map(function(s) { return {tool: s.tool, args: s.args} }); dirty = false; saveError = ""
    }
    function loadNamed(n) { for (var i = 0; i < settings.routines.length; i++) if (settings.routines[i].name === n) { load(i); return } }
    function blank() { flush(); current = -1; old = ""; name = ""; desc = ""; saveError = ""
                       steps = [{tool: "open_app", args: "{\"name\": \"\"}"}]; dirty = true }
    function put(i, k, v) { var a = steps.slice(); a[i] = Object.assign({}, a[i]); a[i][k] = v; steps = a; touched() }
    // while typing: change the step in place - a new array would rebuild every row and take the focus away
    function edit(i, args) { steps[i].args = args; touched() }
    function argsOf(s) { try { var o = JSON.parse(s.args || "{}"); return (o && typeof o === "object") ? o : {} } catch (e) { return {} } }
    function defaultArgs(t) {
        var d = {say: {text: ""}, play_sound: {path: ""}, wait: {seconds: 2}, open_app: {name: ""},
                 volume: {level: 40}, media: {action: "play"}, play_music: {query: ""}, desktop: {action: "switch", target: "1"},
                 open_url: {url: "https://"}, driver_updates: {action: "check"}}
        return JSON.stringify(d[t] || {})
    }
    function setTool(i, t) { var a = steps.slice(); a[i] = {tool: t, args: defaultArgs(t)}; steps = a; touched() }
    // a sound step's args from its parts: how it ends ("once" | "wait" | "until"), how long (s), volume (%)
    function soundArgs(path, end, secs, vol) {
        var o = {path: path || ""}
        if (end === "wait") o.wait = true
        if (end === "until") o.until_done = true
        if (secs > 0) o.seconds = Math.round(secs)
        if (vol !== undefined && vol < 100) o.volume = Math.max(5, Math.round(vol))
        return JSON.stringify(o)
    }
    function soundVol(a) { return a.volume || 100 }
    function soundSecs(a) { return a.seconds || a.min_seconds || 0 }       // (min_seconds: its name up to 0.1.9)
    function soundEnd(a) { return a.until_done === true ? "until" : (a.wait === true ? "wait" : "once") }
    // what the step will do with this file, in words (the length is known for MP3/WAV/FLAC/OGG)
    function soundHint(end, secs, len) {
        function fill(s) { return s.replace("{s}", Math.round(secs)).replace("{len}", len.toFixed(1)) }
        if (end === "until") return fill(secs > 0 ? tr.hint_until_min : tr.hint_until)
        if (secs <= 0) return fill(len > 0 ? tr.hint_whole_len : tr.hint_whole)
        if (len > 0 && secs < len - 0.05) return fill(tr.hint_cut)
        if (len > 0 && secs > len + 0.05) return fill(tr.hint_repeat)
        return fill(tr.hint_for)
    }
    function move(i, d) { var a = steps.slice(); var j = i + d; if (j < 0 || j >= a.length) return; var t = a[i]; a[i] = a[j]; a[j] = t; steps = a; touched() }
    function removeStep(i) { var a = steps.slice(); a.splice(i, 1); steps = a; touched() }
    function save(quiet) {
        if (!dirty || name.trim().length === 0) return
        var res = settings.call("saveRoutine", {name: name, old: old, description: desc, steps: steps, quiet: quiet === true})
        if (res && res.ok) { old = name; dirty = false; saveError = ""
            for (var i = 0; i < settings.routines.length; i++) if (settings.routines[i].name === name) current = i }
        else saveError = (res && res.error) || ""
    }
    Component.onCompleted: if (settings.routines.length > 0) load(0)
    readonly property var templates: [{key: "gaming", label: settings.lang === "de" ? "Spielmodus" : "Gaming mode"},
                                      {key: "work", label: settings.lang === "de" ? "Arbeitsmodus" : "Work mode"},
                                      {key: "music", label: settings.lang === "de" ? "Musikzeit" : "Music time"},
                                      {key: "night", label: settings.lang === "de" ? "Gute Nacht" : "Good night"}]

    RowLayout {
        anchors.fill: parent
        spacing: 16
        ColumnLayout {
            Layout.preferredWidth: 260
            Layout.maximumWidth: 260
            Layout.fillWidth: false
            Layout.fillHeight: true
            spacing: 8
            RowLayout {
                spacing: 6
                Btn { small: true; primary: true; text: "+ " + tab.tr.new_routine; onClicked: tab.blank() }
                Dropdown { objectName: "templates"; implicitWidth: 140; options: tab.templates.map(function(t) { return {value: t.key, label: t.label} })
                           displayText: tab.tr.from_template; currentIndex: -1
                           onChosen: function(v) { tab.flush(); var r = settings.call("addTemplate", {key: v})
                                                   if (r && r.name) tab.loadNamed(r.name) } }
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 4
                model: settings.routines
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: ListView.view.width
                    height: col.implicitHeight + 16
                    radius: 8
                    color: tab.current === index ? tab.th.card2 : (ma.containsMouse ? tab.th.hover : "transparent")
                    border.width: tab.current === index ? 1 : 0
                    border.color: tab.th.accent
                    ColumnLayout { id: col; anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
                        spacing: 2
                        Text { text: modelData.name; color: tab.th.fg; font { pixelSize: 14; weight: Font.DemiBold }
                               Layout.fillWidth: true; elide: Text.ElideRight }
                        Text { text: modelData.summary; color: tab.th.dim; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight } }
                    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: tab.load(index) }
                }
                Text { anchors.centerIn: parent; width: parent.width - 20; visible: settings.routines.length === 0; text: tab.tr.no_routines
                       color: tab.th.dim; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 13 }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 12
            color: tab.th.card
            border.width: 1
            border.color: tab.th.border
            visible: tab.current >= 0 || tab.dirty
            ScrollView {
                anchors { fill: parent; margins: 16 }
                contentWidth: availableWidth
                ColumnLayout {
                    width: parent.width
                    spacing: 10
                    Text { text: tab.tr.routine_hint; color: tab.th.dim; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    Text { objectName: "saveState"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 12
                           text: !tab.dirty ? "✓ " + tab.tr.routine_saved
                                 : tab.saveError ? "⚠ " + tab.tr.routine_not_saved + ": " + tab.saveError
                                 : tab.name.trim().length === 0 ? tab.tr.routine_needs_name : tab.tr.routine_saving
                           color: tab.dirty && tab.saveError ? tab.th.danger : (tab.dirty ? tab.th.dim : tab.th.ok) }
                    RowLayout { spacing: 10; Layout.fillWidth: true
                        Text { text: tab.tr.routine_name; color: tab.th.fg; font.pixelSize: 13; Layout.preferredWidth: 100 }
                        InputField { Layout.fillWidth: true; value: tab.name; onTextEdited: { tab.name = text; tab.touched() } } }
                    RowLayout { spacing: 10; Layout.fillWidth: true
                        Text { text: tab.tr.routine_desc; color: tab.th.fg; font.pixelSize: 13; Layout.preferredWidth: 100 }
                        InputField { Layout.fillWidth: true; value: tab.desc; onTextEdited: { tab.desc = text; tab.touched() } } }
                    Text { text: tab.tr.steps; color: tab.th.fg; font { pixelSize: 14; weight: Font.DemiBold }
                               Layout.topMargin: 6 }
                    Repeater {
                        model: tab.steps
                        Rectangle {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            implicitHeight: stepCol.implicitHeight + 16
                            radius: 8
                            color: tab.th.card2
                            ColumnLayout {
                                id: stepCol
                                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8 }
                                spacing: 6
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Text { text: (index + 1) + "."; color: tab.th.dim; font.pixelSize: 13; Layout.preferredWidth: 22 }
                                    Dropdown { Layout.fillWidth: true; options: settings.toolOptions
                                               value: modelData.tool; onChosen: function(v) { if (v !== modelData.tool) tab.setTool(index, v) } }
                                    Btn { small: true; text: "↑"; enabled: index > 0; onClicked: tab.move(index, -1) }
                                    Btn { small: true; text: "↓"; enabled: index < tab.steps.length - 1; onClicked: tab.move(index, 1) }
                                    Btn { small: true; text: "✕"; onClicked: tab.removeStep(index) }
                                }
                                // Say something: the sentence (spoken when this step is reached)
                                InputField { objectName: "sayField"; visible: modelData.tool === "say"
                                             Layout.fillWidth: true; Layout.leftMargin: 28
                                             value: tab.argsOf(modelData).text || ""; placeholderText: tab.tr.say_text
                                             onTextEdited: tab.edit(index, JSON.stringify({text: text})) }
                                // Play a sound: file picker, how it ends, how long, volume - and in words what that does
                                ColumnLayout { id: snd; visible: modelData.tool === "play_sound"; Layout.fillWidth: true; Layout.leftMargin: 28
                                    spacing: 6
                                    readonly property var a: tab.argsOf(modelData)
                                    // the sound's length: shown, and the hint says what "play for" does with it (cut / repeat)
                                    readonly property real len: (settings && a.path) ? (settings.call("soundInfo", {path: a.path}).seconds || 0) : 0
                                    property real secs: tab.soundSecs(a)            // (follows the field while you type)
                                    RowLayout { Layout.fillWidth: true; spacing: 10
                                        Btn { small: true; text: "♪ " + tab.tr.choose_sound
                                              onClicked: { var r = settings.call("pickSound", {}); var a = tab.argsOf(tab.steps[index])
                                                           if (r && r.path) tab.put(index, "args", tab.soundArgs(r.path, tab.soundEnd(a), tab.soundSecs(a), tab.soundVol(a))) } }
                                        Text { objectName: "soundName"; Layout.fillWidth: true
                                               text: snd.a.path ? snd.a.path.split(/[\\/]/).pop() + (snd.len > 0 ? "  ·  " + snd.len.toFixed(1) + " s" : "") : tab.tr.no_sound
                                               color: snd.a.path ? tab.th.fg : tab.th.dim; elide: Text.ElideMiddle; font.pixelSize: 12 }
                                    }
                                    RowLayout { Layout.fillWidth: true; spacing: 8
                                        Dropdown { objectName: "soundEnd"; Layout.fillWidth: true
                                                   options: [{value: "once", label: tab.tr.sound_once}, {value: "wait", label: tab.tr.sound_wait},
                                                             {value: "until", label: tab.tr.sound_until}]
                                                   value: tab.soundEnd(snd.a)
                                                   onChosen: function(v) { var a = tab.argsOf(tab.steps[index])
                                                                           tab.put(index, "args", tab.soundArgs(a.path, v, tab.soundSecs(a), tab.soundVol(a))) } }
                                        // "play for" = exactly that long; with "until the routine is done": at least that long
                                        Text { objectName: "soundSecondsLabel"; text: tab.soundEnd(snd.a) === "until" ? tab.tr.min_play : tab.tr.play_for
                                               color: tab.th.dim; font.pixelSize: 12 }
                                        InputField { objectName: "soundSeconds"; implicitWidth: 64; value: snd.secs > 0 ? String(snd.secs) : ""
                                                     placeholderText: tab.soundEnd(snd.a) === "until" ? "0" : tab.tr.sound_all
                                                     onTextEdited: { var t = text.trim().replace(",", "."); var n = t === "" ? 0 : parseFloat(t)
                                                                     var a = tab.argsOf(tab.steps[index])
                                                                     if (!isNaN(n) && n >= 0) { snd.secs = n; tab.edit(index, tab.soundArgs(a.path, tab.soundEnd(a), n, tab.soundVol(a))) } } }
                                        Text { text: tab.tr.seconds_short; color: tab.th.dim; font.pixelSize: 12 }
                                        Text { text: tab.tr.volume_short; color: tab.th.dim; font.pixelSize: 12; Layout.leftMargin: 6 }
                                        InputField { objectName: "soundVolume"; implicitWidth: 64; value: String(tab.soundVol(snd.a))
                                                     onTextEdited: { var n = parseFloat(text.replace(",", ".")); var a = tab.argsOf(tab.steps[index])
                                                                     if (!isNaN(n) && n >= 5 && n <= 100) tab.edit(index, tab.soundArgs(a.path, tab.soundEnd(a), tab.soundSecs(a), n)) } }
                                        Text { text: "%"; color: tab.th.dim; font.pixelSize: 12 }
                                    }
                                    Text { objectName: "soundHint"; visible: !!snd.a.path; Layout.fillWidth: true; wrapMode: Text.Wrap
                                           text: tab.soundHint(tab.soundEnd(snd.a), snd.secs, snd.len); color: tab.th.dim; font.pixelSize: 11 }
                                }
                                // Check / update drivers
                                Dropdown { visible: modelData.tool === "driver_updates"; Layout.fillWidth: true; Layout.leftMargin: 28
                                           options: [{value: "check", label: tab.tr.drivers_check}, {value: "install", label: tab.tr.drivers_install}]
                                           value: tab.argsOf(modelData).action || "check"
                                           onChosen: function(v) { tab.put(index, "args", JSON.stringify({action: v})) } }
                                // Wait: seconds
                                RowLayout { visible: modelData.tool === "wait"; Layout.leftMargin: 28; spacing: 8
                                    InputField { implicitWidth: 90; value: String(tab.argsOf(modelData).seconds || 1)
                                                 onTextEdited: { var n = parseFloat(text.replace(",", "."))
                                                                 if (!isNaN(n)) tab.edit(index, JSON.stringify({seconds: n})) } }
                                    Text { text: tab.tr.wait_seconds; color: tab.th.dim; font.pixelSize: 12 }
                                }
                                // everything else: the details as JSON
                                InputField { objectName: "argsField"; visible: ["say", "play_sound", "wait", "driver_updates"].indexOf(modelData.tool) < 0
                                             Layout.fillWidth: true; Layout.leftMargin: 28; value: modelData.args
                                             placeholderText: tab.tr.args
                                             font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"
                                             onTextEdited: tab.edit(index, text) }
                            }
                        }
                    }
                    Btn { small: true; text: "+ " + tab.tr.add_step
                          onClicked: { var a = tab.steps.slice(); a.push({tool: "open_app", args: "{\"name\": \"\"}"}); tab.steps = a; tab.touched() } }
                    Flow {
                        Layout.fillWidth: true
                        Layout.topMargin: 10
                        spacing: 8
                        Btn { primary: true; enabled: tab.dirty && tab.name.trim().length > 0; text: tab.tr.save; onClicked: tab.save() }
                        Btn { enabled: !tab.dirty && tab.current >= 0; text: "▶ " + tab.tr.run_now; onClicked: settings.call("runRoutine", {name: tab.old}) }
                        DangerBtn { visible: tab.current >= 0; normalText: tab.tr.delete; confirmText: tab.tr.confirm_again
                                    onConfirmed: { settings.call("deleteRoutine", {name: tab.old}); tab.current = -1; tab.dirty = false
                                                   if (settings.routines.length) tab.load(0) } }
                    }
                }
            }
        }
    }
}
