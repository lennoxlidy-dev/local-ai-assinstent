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
    function set(k, v) { settings.call("set", {key: k, value: v}) }
    ColumnLayout {
        width: tab.availableWidth - 14
        spacing: 14
        Card {
            title: tab.tr.sec_identity
            SettingRow {
                label: tab.tr.assistant_name; hint: tab.tr.assistant_name_hint
                InputField { value: tab.c["general.assistant_name"] || ""; onCommitted: function(t) { tab.set("general.assistant_name", t) } }
            }
            SettingRow {
                label: tab.tr.user_name
                InputField { value: tab.c["general.user_name"] || ""; onCommitted: function(t) { tab.set("general.user_name", t) } }
            }
            SettingRow {
                label: tab.tr.language
                Dropdown { options: [{value: "en", label: "English"}, {value: "de", label: "Deutsch"}]
                           value: tab.c["general.language"]; onChosen: function(v) { tab.set("general.language", v) } }
            }
            SettingRow {
                label: tab.tr.personality
                Dropdown { options: ["friendly", "professional", "witty", "calm"].map(function(k) { return {value: k, label: tab.tr["p_" + k]} })
                           value: tab.c["general.personality"]; onChosen: function(v) { tab.set("general.personality", v) } }
            }
            SettingRow {
                label: tab.tr.answer_length; separator: false
                Dropdown { options: ["short", "normal", "detailed"].map(function(k) { return {value: k, label: tab.tr["l_" + k]} })
                           value: tab.c["general.answer_length"]; onChosen: function(v) { tab.set("general.answer_length", v) } }
            }
        }
        Card {
            title: tab.tr.sec_profile; hint: tab.tr.sec_profile_hint
            Text { text: tab.tr.about; color: tab.th.fg; font.pixelSize: 13; Layout.topMargin: 6 }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 84
                Layout.topMargin: 4
                radius: 8
                color: tab.th.field
                border.width: about.activeFocus ? 2 : 1
                border.color: about.activeFocus ? tab.th.accent : tab.th.border
                TextArea {
                    id: about
                    anchors.fill: parent
                    text: tab.c["profile.about"] || ""
                    wrapMode: TextArea.Wrap
                    color: tab.th.fg
                    font.pixelSize: 13
                    padding: 8
                    background: null
                    onActiveFocusChanged: if (!activeFocus && text !== (tab.c["profile.about"] || "")) tab.set("profile.about", text.substring(0, 400))
                }
            }
            SettingRow {
                label: tab.tr.location; separator: false
                InputField { value: tab.c["profile.location"] || ""; onCommitted: function(t) { tab.set("profile.location", t) } }
            }
        }
        Card {
            title: tab.tr.sec_startup
            SettingRow {
                label: tab.tr.autostart; hint: tab.tr.autostart_hint
                ToggleSwitch { checked: tab.c["general.autostart"] === true; onToggled: function(on) { tab.set("general.autostart", on) } }
            }
            SettingRow {
                label: tab.tr.startup_delay; hint: tab.tr.startup_delay_hint; separator: false
                ValueSlider { from: 0; to: 60; step: 1; decimals: 0; unit: tab.tr.seconds
                              value: tab.c["general.startup_delay_s"] || 0; onCommitted: function(v) { tab.set("general.startup_delay_s", v) } }
            }
        }
        Card {
            title: tab.tr.sec_setup
            Flow {
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 8
                Btn { primary: true; text: tab.tr.run_setup; onClicked: settings.call("runSetup", {}) }
                Btn { text: tab.tr.edit_config; onClicked: settings.call("open", {what: "config"}) }
                Btn { text: tab.tr.open_data; onClicked: settings.call("open", {what: "data"}) }
                Btn { text: tab.tr.open_logs; onClicked: settings.call("open", {what: "logs"}) }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
