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
            title: tab.tr.sec_conv
            SettingRow {
                label: tab.tr.save_conv
                ToggleSwitch { checked: tab.c["privacy.save_conversations"] === true; onToggled: function(on) { tab.set("privacy.save_conversations", on) } }
            }
            SettingRow {
                label: tab.tr.retention
                Dropdown { implicitWidth: 160; options: [7, 30, 90, 365, 0].map(function(d) { return {value: d, label: tab.tr["ret_" + d]} })
                           value: tab.c["privacy.retention_days"]; onChosen: function(v) { tab.set("privacy.retention_days", v) } }
            }
            SettingRow {
                label: tab.tr.delete_all_conv; separator: false
                DangerBtn { small: true; normalText: tab.tr.delete; confirmText: tab.tr.confirm_again; onConfirmed: settings.call("deleteAllConversations", {}) }
            }
        }
        Card {
            title: tab.tr.sec_memory; hint: tab.tr.memory_hint
            Repeater {
                model: settings.facts
                RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    spacing: 10
                    InputField { Layout.fillWidth: true; value: modelData.text
                                 onCommitted: function(t) { settings.call("updateFact", {id: modelData.id, text: t}) } }
                    Text { text: modelData.when; color: tab.th.dim; font.pixelSize: 11 }
                    Btn { small: true; text: "✕"; onClicked: settings.call("deleteFact", {id: modelData.id}) }
                }
            }
            Text { visible: settings.facts.length === 0; text: tab.tr.no_facts; color: tab.th.dim; font.pixelSize: 13; Layout.topMargin: 6 }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 10
                spacing: 8
                InputField { id: newFact; Layout.fillWidth: true; placeholderText: tab.tr.add_fact
                             onAccepted: { if (settings.call("addFact", {text: text}).ok) text = "" } }
                Btn { text: tab.tr.add; enabled: newFact.text.trim().length > 0
                      onClicked: { if (settings.call("addFact", {text: newFact.text}).ok) newFact.text = "" } }
                DangerBtn { visible: settings.facts.length > 0; normalText: tab.tr.forget_all; confirmText: tab.tr.confirm_again
                            onConfirmed: settings.call("forgetAll", {}) }
            }
        }
        Card {
            title: tab.tr.sec_audio
            SettingRow {
                label: tab.tr.save_audio; hint: tab.tr.save_audio_hint
                ToggleSwitch { checked: tab.c["privacy.save_audio"] === true; onToggled: function(on) { tab.set("privacy.save_audio", on) } }
            }
            SettingRow {
                label: (settings.recordings.text || ""); separator: false
                Row { spacing: 8
                    Btn { small: true; text: tab.tr.open_recordings; onClicked: settings.call("open", {what: "recordings"}) }
                    DangerBtn { small: true; enabled: (settings.recordings.count || 0) > 0; normalText: tab.tr.delete_recordings
                                confirmText: tab.tr.confirm_again; onConfirmed: settings.call("deleteRecordings", {}) } }
            }
        }
        Card {
            title: tab.tr.sec_logs
            SettingRow {
                label: tab.tr.log_transcripts; hint: tab.tr.log_transcripts_hint; separator: false
                ToggleSwitch { checked: tab.c["privacy.log_transcripts"] === true; onToggled: function(on) { tab.set("privacy.log_transcripts", on) } }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
