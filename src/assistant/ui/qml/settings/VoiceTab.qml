import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../common"

ScrollView {
    id: tab
    contentWidth: availableWidth
    readonly property var c: settings.cfg
    readonly property var tr: settings.tr
    readonly property var th: look.colors
    readonly property var v: settings.voice || ({})
    readonly property var calib: settings.calib || ({})
    readonly property var enr: settings.enroll || ({})
    readonly property var voices: ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"].map(function(x) {
        return {value: x, label: x + (x.charAt(0) === "F" ? " ♀" : " ♂")} })
    function set(k, val) { settings.call("set", {key: k, value: val}) }
    function fill(s, o) { for (var k in o) s = s.replace("{" + k + "}", o[k]); return s }
    ColumnLayout {
        width: tab.availableWidth - 14
        spacing: 14
        Card {
            visible: tab.v.installed === false
            title: tab.tr.voice_off
            Btn { Layout.topMargin: 6; primary: true; text: tab.tr.download_models; onClicked: settings.call("downloadModels", {}) }
            Text { visible: (settings.download.text || "") !== ""; text: settings.download.text || ""; color: tab.th.dim; font.pixelSize: 12
                   Layout.fillWidth: true; Layout.topMargin: 4; wrapMode: Text.Wrap }
        }
        Card {
            title: tab.tr.sec_wake
            SettingRow {
                label: tab.tr.wake_enabled + " “" + (tab.c["general.assistant_name"] || "") + "”"
                ToggleSwitch { checked: tab.c["voice.wake_word_enabled"] === true; onToggled: function(on) { tab.set("voice.wake_word_enabled", on) } }
            }
            SettingRow {
                label: tab.tr.sensitivity; hint: tab.tr.sensitivity_hint
                ValueSlider { from: 0; to: 1; step: 0.05; decimals: 2; value: tab.c["voice.wake_sensitivity"] || 0
                              onCommitted: function(x) { tab.set("voice.wake_sensitivity", x) } }
            }
            SettingRow {
                label: tab.tr.calibrate; hint: tab.calib.step === "recording" ? tab.fill(tab.tr.say_now, {name: tab.c["general.assistant_name"], n: tab.calib.n, total: tab.calib.total})
                                               : tab.calib.step === "processing" ? tab.tr.checking
                                               : tab.calib.step === "done" ? tab.calib.text : tab.tr.calibrate_hint
                Btn { text: tab.tr.calibrate; enabled: tab.v.installed === true && !(tab.calib.step === "starting" || tab.calib.step === "recording" || tab.calib.step === "processing")
                      onClicked: settings.call("calibrate", {}) }
            }
            SettingRow {
                visible: (tab.c["voice.wake_variants"] || []).length > 0
                label: tab.tr.variants; hint: tab.tr.variants_hint
                Row {
                    spacing: 6
                    Repeater {
                        model: tab.c["voice.wake_variants"] || []
                        Rectangle {
                            required property string modelData
                            height: 28; radius: 14; width: chip.implicitWidth + 36
                            color: tab.th.card2; border.width: 1; border.color: tab.th.border
                            Text { id: chip; x: 12; anchors.verticalCenter: parent.verticalCenter; text: modelData; color: tab.th.fg; font.pixelSize: 12 }
                            Text { anchors { right: parent.right; rightMargin: 10; verticalCenter: parent.verticalCenter }
                               text: "✕"; color: tab.th.dim; font.pixelSize: 11
                                   MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                                               onClicked: settings.call("removeVariant", {variant: modelData}) } }
                        }
                    }
                }
            }
            SettingRow {
                label: tab.tr.double_check; hint: tab.tr.double_check_hint; separator: false
                ToggleSwitch { checked: tab.c["voice.wake_double_check"] === true; onToggled: function(on) { tab.set("voice.wake_double_check", on) } }
            }
        }
        Card {
            title: tab.tr.sec_listen
            SettingRow {
                label: tab.tr.silence
                ValueSlider { from: 0.3; to: 3; step: 0.1; value: tab.c["voice.silence_timeout_s"] || 1; unit: tab.tr.seconds
                              onCommitted: function(x) { tab.set("voice.silence_timeout_s", x) } }
            }
            SettingRow {
                label: tab.tr.max_len
                ValueSlider { from: 5; to: 120; step: 5; decimals: 0; value: tab.c["voice.max_utterance_s"] || 20; unit: tab.tr.seconds
                              onCommitted: function(x) { tab.set("voice.max_utterance_s", x) } }
            }
            SettingRow {
                label: tab.tr.follow_up; hint: tab.tr.follow_up_hint
                ToggleSwitch { checked: tab.c["voice.follow_up"] === true; onToggled: function(on) { tab.set("voice.follow_up", on) } }
            }
            SettingRow {
                visible: tab.c["voice.follow_up"] === true
                label: tab.tr.follow_up_s
                ValueSlider { from: 1; to: 30; step: 1; decimals: 0; value: tab.c["voice.follow_up_s"] || 5; unit: tab.tr.seconds
                              onCommitted: function(x) { tab.set("voice.follow_up_s", x) } }
            }
            SettingRow {
                label: tab.tr.live_transcript
                ToggleSwitch { checked: tab.c["voice.live_transcript"] === true; onToggled: function(on) { tab.set("voice.live_transcript", on) } }
            }
            SettingRow {
                label: tab.tr.ptt_mode
                Segmented { options: [{value: "press", label: tab.tr.ptt_press}, {value: "hold", label: tab.tr.ptt_hold}]
                            value: tab.c["voice.push_to_talk_mode"]; onChosen: function(x) { tab.set("voice.push_to_talk_mode", x) } }
            }
            SettingRow {
                label: tab.tr.hk_ptt
                KeyField { value: tab.c["hotkeys.push_to_talk"] || ""; pressText: tab.tr.press_keys; changeText: tab.tr.change
                           onCapturingChanged: settings.call("suspendHotkeys", {on: capturing})
                           onCaptured: function(spec) { tab.set("hotkeys.push_to_talk", spec) } }
            }
            SettingRow {
                label: tab.tr.hk_panic; separator: false
                KeyField { value: tab.c["hotkeys.panic"] || ""; pressText: tab.tr.press_keys; changeText: tab.tr.change
                           onCapturingChanged: settings.call("suspendHotkeys", {on: capturing})
                           onCaptured: function(spec) { tab.set("hotkeys.panic", spec) } }
            }
        }
        Card {
            title: tab.tr.sec_tts
            SettingRow {
                label: tab.tr.tts_enabled
                ToggleSwitch { checked: tab.c["voice.tts_enabled"] === true; onToggled: function(on) { tab.set("voice.tts_enabled", on) } }
            }
            SettingRow {
                label: tab.tr.voice_en
                Row { spacing: 8
                    Dropdown { implicitWidth: 120; options: tab.voices; value: tab.c["voice.tts_voice_en"]; onChosen: function(x) { tab.set("voice.tts_voice_en", x) } }
                    Btn { small: true; text: "▶ " + tab.tr.preview; onClicked: settings.call("preview", {voice: tab.c["voice.tts_voice_en"], lang: "en"}) } }
            }
            SettingRow {
                label: tab.tr.voice_de
                Row { spacing: 8
                    Dropdown { implicitWidth: 120; options: tab.voices; value: tab.c["voice.tts_voice_de"]; onChosen: function(x) { tab.set("voice.tts_voice_de", x) } }
                    Btn { small: true; text: "▶ " + tab.tr.preview; onClicked: settings.call("preview", {voice: tab.c["voice.tts_voice_de"], lang: "de"}) } }
            }
            SettingRow {
                label: tab.tr.speed
                ValueSlider { from: 0.5; to: 2; step: 0.05; decimals: 2; value: tab.c["voice.tts_speed"] || 1; unit: "×"
                              onCommitted: function(x) { tab.set("voice.tts_speed", x) } }
            }
            SettingRow {
                label: tab.tr.tts_volume; hint: tab.tr.tts_volume_hint
                ValueSlider { objectName: "ttsVolume"; from: 50; to: 200; step: 5; decimals: 0; unit: "%"
                              value: tab.c["voice.tts_volume"] || 100
                              onCommitted: function(x) { tab.set("voice.tts_volume", Math.round(x)) } }
            }
            SettingRow {
                label: tab.tr.duck; hint: tab.tr.duck_hint
                ValueSlider { objectName: "duckPercent"; from: 0; to: 100; step: 5; decimals: 0; unit: "%"
                              value: tab.c["voice.duck_percent"] !== undefined ? tab.c["voice.duck_percent"] : 25
                              onCommitted: function(x) { tab.set("voice.duck_percent", Math.round(x)) } }
            }
            SettingRow {
                label: tab.tr.chime; separator: false
                ToggleSwitch { checked: tab.c["voice.chime"] === true; onToggled: function(on) { tab.set("voice.chime", on) } }
            }
        }
        Card {
            title: tab.tr.sec_devices
            SettingRow {
                label: tab.tr.input
                Dropdown { implicitWidth: 300; options: settings.devicesIn; value: tab.c["voice.input_device"]; onChosen: function(x) { tab.set("voice.input_device", x) } }
            }
            SettingRow {
                label: tab.tr.level
                Rectangle {
                    width: 300; height: 10; radius: 5; color: tab.th.card2
                    Rectangle { height: parent.height; radius: 5; color: tab.th.ok
                                width: parent.width * Math.min(1, Math.sqrt(settings.level * 12))
                                Behavior on width { NumberAnimation { duration: 60 } } }
                }
            }
            SettingRow {
                label: tab.tr.output; separator: false
                Row { spacing: 8
                    Dropdown { implicitWidth: 300; options: settings.devicesOut; value: tab.c["voice.output_device"]; onChosen: function(x) { tab.set("voice.output_device", x) } }
                    Btn { small: true; text: tab.tr.test; onClicked: settings.call("testSound", {}) } }
            }
        }
        Card {
            title: tab.tr.sec_lock; hint: tab.tr.lock_hint
            SettingRow {
                label: tab.tr.lock_enabled
                hint: tab.v.enrolled ? tab.tr.enrolled_yes : tab.tr.enrolled_no
                ToggleSwitch { enabled: tab.v.enrolled === true; checked: tab.c["voice.voice_lock"] === true
                               onToggled: function(on) { tab.set("voice.voice_lock", on) } }
            }
            ColumnLayout {
                visible: tab.enr.step === "recording" || tab.enr.step === "processing"
                Layout.fillWidth: true
                Layout.topMargin: 8
                spacing: 4
                Text { text: tab.enr.step === "processing" ? tab.tr.checking : tab.fill(tab.tr.read_aloud, {n: tab.enr.n, total: tab.enr.total})
                       color: tab.th.dim; font.pixelSize: 12 }
                Text { visible: tab.enr.step === "recording"; text: tab.enr.prompt || ""; color: tab.th.fg; font.pixelSize: 18
                       wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
            SettingRow {
                label: tab.tr.threshold
                ValueSlider { from: 0.2; to: 0.95; step: 0.05; decimals: 2; value: tab.c["voice.voice_lock_threshold"] || 0.55
                              onCommitted: function(x) { tab.set("voice.voice_lock_threshold", x) } }
            }
            Flow {
                Layout.fillWidth: true
                Layout.topMargin: 10
                spacing: 8
                Btn { primary: !tab.v.enrolled; enabled: tab.v.lockInstalled === true && !(tab.enr.step === "recording" || tab.enr.step === "processing" || tab.enr.step === "starting")
                      text: tab.v.enrolled ? tab.tr.re_enroll : tab.tr.enroll; onClicked: settings.call("enroll", {}) }
                DangerBtn { visible: tab.v.enrolled === true; normalText: tab.tr.forget_voice; confirmText: tab.tr.confirm_again
                            onConfirmed: settings.call("forgetVoice", {}) }
            }
        }
        Card {
            title: tab.tr.sec_voice_adv
            SettingRow {
                label: tab.tr.keep_loaded; hint: tab.tr.keep_loaded_hint; separator: false
                ValueSlider { from: 0; to: 120; step: 5; decimals: 0; value: tab.c["voice.stt_keep_loaded_min"] || 0; unit: tab.tr.minutes
                              onCommitted: function(x) { tab.set("voice.stt_keep_loaded_min", x) } }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
