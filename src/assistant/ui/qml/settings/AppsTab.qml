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
    readonly property var appOptions: (settings.apps || []).map(function(a) { return {value: a, label: a} })
    function set(k, v) { settings.call("set", {key: k, value: v}) }
    function withCurrent(opts, cur) { return cur && !opts.some(function(o) { return o.value === cur }) ? [{value: cur, label: cur}].concat(opts) : opts }
    ColumnLayout {
        width: tab.availableWidth - 14
        spacing: 14
        Card {
            title: tab.tr.sec_default_apps
            SettingRow {
                label: tab.tr.music_app
                Dropdown { implicitWidth: 300; options: tab.withCurrent(tab.appOptions, tab.c["apps.music_app"]); value: tab.c["apps.music_app"]
                           onChosen: function(v) { tab.set("apps.music_app", v) } }
            }
            SettingRow {
                label: tab.tr.browser
                Dropdown { implicitWidth: 300; options: [{value: "", label: tab.tr.default_browser}].concat(tab.withCurrent(tab.appOptions, tab.c["apps.browser"]))
                           value: tab.c["apps.browser"]; onChosen: function(v) { tab.set("apps.browser", v) } }
            }
            SettingRow {
                label: tab.tr.refresh_apps; hint: (tab.tr.apps_count || "").replace("{n}", (settings.apps || []).length); separator: false
                Btn { small: true; text: tab.tr.refresh; onClicked: settings.call("refreshApps", {}) }
            }
        }
        Card {
            title: tab.tr.sec_aliases; hint: tab.tr.aliases_hint
            Repeater {
                model: settings.aliases
                SettingRow {
                    required property var modelData
                    label: "“" + modelData.alias + "”"
                    Row { spacing: 8
                        Text { text: "→"; color: tab.th.dim; anchors.verticalCenter: parent.verticalCenter }
                        Dropdown { implicitWidth: 260; options: tab.withCurrent(tab.appOptions, modelData.app); value: modelData.app
                                   onChosen: function(v) { settings.call("setAlias", {alias: modelData.alias, app: v}) } }
                        Btn { small: true; text: "✕"; onClicked: settings.call("removeAlias", {alias: modelData.alias}) } }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 10
                spacing: 8
                InputField { id: newAlias; implicitWidth: 200; placeholderText: tab.tr.alias }
                Text { text: "→"; color: tab.th.dim }
                InputField { id: newApp; Layout.fillWidth: true; placeholderText: tab.tr.app }
                Btn { text: tab.tr.add; enabled: newAlias.text.trim().length > 0 && newApp.text.trim().length > 0
                      onClicked: { if (settings.call("setAlias", {alias: newAlias.text, app: newApp.text}).ok) { newAlias.text = ""; newApp.text = "" } } }
            }
        }
        Card {
            title: tab.tr.sec_desktops
            SettingRow {
                label: tab.tr.desktops_enabled
                ToggleSwitch { checked: tab.c["desktops.enabled"] === true; onToggled: function(on) { tab.set("desktops.enabled", on) } }
            }
            SettingRow {
                visible: tab.c["desktops.enabled"] === true
                label: tab.tr.desktop_name
                InputField { implicitWidth: 200; value: tab.c["desktops.name"] || ""; onCommitted: function(t) { tab.set("desktops.name", t) } }
            }
            SettingRow {
                visible: tab.c["desktops.enabled"] === true
                label: tab.tr.default_rule
                Segmented { options: [{value: "current", label: tab.tr.rule_current}, {value: "assistant", label: tab.tr.rule_assistant}]
                            value: tab.c["desktops.default_rule"]; onChosen: function(v) { tab.set("desktops.default_rule", v) } }
            }
            Repeater {
                model: tab.c["desktops.enabled"] === true ? settings.desktopRules : []
                SettingRow {
                    required property var modelData
                    label: modelData.app
                    Row { spacing: 8
                        Segmented { options: [{value: "current", label: tab.tr.rule_current}, {value: "assistant", label: tab.tr.rule_assistant}]
                                    value: modelData.rule; onChosen: function(v) { settings.call("setDesktopRule", {app: modelData.app, rule: v}) } }
                        Btn { small: true; text: "✕"; onClicked: settings.call("removeDesktopRule", {app: modelData.app}) } }
                }
            }
            RowLayout {
                visible: tab.c["desktops.enabled"] === true
                Layout.fillWidth: true
                Layout.topMargin: 10
                spacing: 8
                InputField { id: ruleApp; Layout.fillWidth: true; placeholderText: tab.tr.app }
                Btn { text: tab.tr.add; enabled: ruleApp.text.trim().length > 0
                      onClicked: { if (settings.call("setDesktopRule", {app: ruleApp.text, rule: "assistant"}).ok) ruleApp.text = "" } }
            }
        }
        Card {
            title: tab.tr.sec_files
            SettingRow {
                label: tab.tr.use_everything
                ToggleSwitch { checked: tab.c["files.use_everything"] === true; onToggled: function(on) { tab.set("files.use_everything", on) } }
            }
            SettingRow {
                label: tab.tr.index_roots; separator: false
                Btn { small: true; text: "+ " + tab.tr.add_folder; onClicked: settings.call("addIndexRoot", {}) }
            }
            Repeater {
                model: tab.c["files.index_roots"] || []
                SettingRow {
                    required property string modelData
                    label: modelData
                    Btn { small: true; text: "✕"; onClicked: settings.call("removeIndexRoot", {path: modelData}) }
                }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
