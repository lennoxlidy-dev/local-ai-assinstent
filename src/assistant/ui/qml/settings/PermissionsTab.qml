import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../common"

ScrollView {
    id: tab
    contentWidth: availableWidth
    readonly property var tr: settings.tr
    readonly property var th: look.colors
    ColumnLayout {
        width: tab.availableWidth - 14
        spacing: 14
        Card {
            title: tab.tr.sec_caps; hint: tab.tr.caps_hint
            Repeater {
                model: settings.caps
                SettingRow {
                    required property var modelData
                    required property int index
                    label: modelData.title; hint: modelData.hint
                    separator: index < settings.caps.length - 1
                    Segmented { options: modelData.options; value: modelData.level
                                onChosen: function(v) { settings.call("setLevel", {cap: modelData.id, level: v}) } }
                }
            }
        }
        Card {
            title: tab.tr.sec_risks; hint: tab.tr.risks_hint
            Repeater {
                model: settings.risks
                SettingRow {
                    required property var modelData
                    required property int index
                    label: modelData.title
                    hint: modelData.locked ? tab.tr.always : ""
                    separator: index < settings.risks.length - 1
                    ToggleSwitch { enabled: !modelData.locked; checked: modelData.on
                                   onToggled: function(on) { settings.call("setRisk", {risk: modelData.id, on: on}) } }
                }
            }
        }
        Card {
            title: tab.tr.sec_folders; hint: tab.tr.folders_hint
            Btn { Layout.topMargin: 6; Layout.bottomMargin: 6; text: "+ " + tab.tr.add_folder; onClicked: settings.call("addFolder", {}) }
            Repeater {
                model: settings.folders
                SettingRow {
                    required property var modelData
                    required property int index
                    label: modelData.path; hint: modelData.note || ""
                    separator: index < settings.folders.length - 1
                    Row { spacing: 8
                        Dropdown { implicitWidth: 160; options: settings.accessOptions; value: modelData.access
                                   onChosen: function(v) { settings.call("setFolder", {path: modelData.path, access: v, note: modelData.note || ""}) } }
                        Btn { small: true; text: "✕"; onClicked: settings.call("removeFolder", {path: modelData.path}) } }
                }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
