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
    function load(i) {
        var r = settings.routines[i]
        current = i; old = r.name; name = r.name; desc = r.description || ""
        steps = r.steps.map(function(s) { return {tool: s.tool, args: s.args} }); dirty = false
    }
    function blank() { current = -1; old = ""; name = ""; desc = ""; steps = [{tool: "open_app", args: "{\"name\": \"\"}"}]; dirty = true }
    function put(i, k, v) { var a = steps.slice(); a[i] = Object.assign({}, a[i]); a[i][k] = v; steps = a; dirty = true }
    // while typing: change the step in place - a new array would rebuild every row and take the focus away
    function edit(i, args) { steps[i].args = args; dirty = true }
    function argsOf(s) { try { var o = JSON.parse(s.args || "{}"); return (o && typeof o === "object") ? o : {} } catch (e) { return {} } }
    function defaultArgs(t) {
        var d = {say: {text: ""}, play_sound: {path: ""}, wait: {seconds: 2}, open_app: {name: ""},
                 volume: {level: 40}, media: {action: "play"}, play_music: {query: ""}, desktop: {action: "switch", target: "1"},
                 open_url: {url: "https://"}, driver_updates: {action: "check"}}
        return JSON.stringify(d[t] || {})
    }
    function setTool(i, t) { var a = steps.slice(); a[i] = {tool: t, args: defaultArgs(t)}; steps = a; dirty = true }
    // a sound step's args from its parts: how it ends ("once" | "wait" | "until") and the minimum seconds
    function soundArgs(path, end, minS) {
        var o = {path: path || ""}
        if (end === "wait") o.wait = true
        if (end === "until") o.until_done = true
        if (minS > 0) o.min_seconds = Math.round(minS)
        return JSON.stringify(o)
    }
    function soundEnd(a) { return a.until_done === true ? "until" : (a.wait === true ? "wait" : "once") }
    function move(i, d) { var a = steps.slice(); var j = i + d; if (j < 0 || j >= a.length) return; var t = a[i]; a[i] = a[j]; a[j] = t; steps = a; dirty = true }
    function removeStep(i) { var a = steps.slice(); a.splice(i, 1); steps = a; dirty = true }
    function save() {
        var res = settings.call("saveRoutine", {name: name, old: old, description: desc, steps: steps})
        if (res.ok) { old = name; dirty = false
            for (var i = 0; i < settings.routines.length; i++) if (settings.routines[i].name === name) current = i }
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
                Dropdown { implicitWidth: 140; options: tab.templates.map(function(t) { return {value: t.key, label: t.label} })
                           displayText: tab.tr.from_template; currentIndex: -1
                           onChosen: function(v) { settings.call("addTemplate", {key: v}); if (settings.routines.length) tab.load(settings.routines.length - 1) } }
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
                    RowLayout { spacing: 10; Layout.fillWidth: true
                        Text { text: tab.tr.routine_name; color: tab.th.fg; font.pixelSize: 13; Layout.preferredWidth: 100 }
                        InputField { Layout.fillWidth: true; value: tab.name; onTextEdited: { tab.name = text; tab.dirty = true } } }
                    RowLayout { spacing: 10; Layout.fillWidth: true
                        Text { text: tab.tr.routine_desc; color: tab.th.fg; font.pixelSize: 13; Layout.preferredWidth: 100 }
                        InputField { Layout.fillWidth: true; value: tab.desc; onTextEdited: { tab.desc = text; tab.dirty = true } } }
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
                                // Play a sound: file picker, how it ends, minimum time
                                ColumnLayout { visible: modelData.tool === "play_sound"; Layout.fillWidth: true; Layout.leftMargin: 28
                                    spacing: 6
                                    readonly property var a: tab.argsOf(modelData)
                                    RowLayout { Layout.fillWidth: true; spacing: 10
                                        Btn { small: true; text: "♪ " + tab.tr.choose_sound
                                              onClicked: { var r = settings.call("pickSound", {}); var a = parent.parent.a
                                                           if (r && r.path) tab.put(index, "args", tab.soundArgs(r.path, tab.soundEnd(a), a.min_seconds || 0)) } }
                                        Text { Layout.fillWidth: true; text: parent.parent.a.path ? parent.parent.a.path.split(/[\\/]/).pop() : tab.tr.no_sound
                                               color: parent.parent.a.path ? tab.th.fg : tab.th.dim; elide: Text.ElideMiddle; font.pixelSize: 12 }
                                    }
                                    RowLayout { Layout.fillWidth: true; spacing: 8
                                        Dropdown { objectName: "soundEnd"; Layout.fillWidth: true
                                                   options: [{value: "once", label: tab.tr.sound_once}, {value: "wait", label: tab.tr.sound_wait},
                                                             {value: "until", label: tab.tr.sound_until}]
                                                   value: tab.soundEnd(parent.parent.a)
                                                   onChosen: function(v) { var a = parent.parent.a
                                                                           tab.put(index, "args", tab.soundArgs(a.path, v, a.min_seconds || 0)) } }
                                        Text { text: tab.tr.min_play; color: tab.th.dim; font.pixelSize: 12 }
                                        InputField { objectName: "soundMin"; implicitWidth: 64; value: String(parent.parent.a.min_seconds || 0)
                                                     onTextEdited: { var n = parseFloat(text.replace(",", ".")); var a = parent.parent.a
                                                                     if (!isNaN(n) && n >= 0) tab.edit(index, tab.soundArgs(a.path, tab.soundEnd(a), n)) } }
                                        Text { text: tab.tr.seconds_short; color: tab.th.dim; font.pixelSize: 12 }
                                    }
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
                          onClicked: { var a = tab.steps.slice(); a.push({tool: "open_app", args: "{\"name\": \"\"}"}); tab.steps = a; tab.dirty = true } }
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
