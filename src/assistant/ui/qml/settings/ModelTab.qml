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
    readonly property var llm: settings.llm || ({})
    readonly property var dl: settings.download || ({})
    readonly property bool missing: (settings.models || []).some(function(m) {
        return (m.value === tab.c["llm.model_smart"] || m.value === tab.c["llm.model_fast"]) && !m.installed })
    function set(k, v) { settings.call("set", {key: k, value: v}) }
    ColumnLayout {
        width: tab.availableWidth - 14
        spacing: 14
        Card {
            title: tab.tr.sec_status
            SettingRow {
                label: tab.tr.model_state + (tab.llm.model ? ": " + tab.llm.model : "")
                hint: (tab.llm.status || "") + (tab.llm.reason && !tab.llm.ready ? " · " + tab.llm.reason : "")
                Row { spacing: 10
                    Rectangle { width: 10; height: 10; radius: 5; anchors.verticalCenter: parent.verticalCenter
                                color: tab.llm.ready ? tab.th.ok : tab.th.warn }
                    Btn { small: true; text: tab.tr.restart_model; onClicked: settings.call("restartModel", {}) } }
            }
            SettingRow {
                visible: tab.missing || tab.dl.active === true || (tab.dl.text || "") !== ""
                label: tab.tr.download_models; hint: tab.dl.text || ""; separator: false
                Btn { primary: true; enabled: tab.dl.active !== true; text: tab.tr.download_models
                      onClicked: settings.call("downloadModels", {}) }
            }
        }
        Card {
            title: tab.tr.sec_model; hint: tab.tr.apply_restart
            SettingRow {
                label: tab.tr.profile
                Segmented { options: [{value: "auto", label: tab.tr.pr_auto}, {value: "smart", label: tab.tr.pr_smart}, {value: "fast", label: tab.tr.pr_fast}]
                            value: tab.c["llm.profile"]; onChosen: function(v) { tab.set("llm.profile", v) } }
            }
            SettingRow {
                label: tab.tr.smart_model
                Dropdown { implicitWidth: 380; options: settings.models; value: tab.c["llm.model_smart"]; onChosen: function(v) { tab.set("llm.model_smart", v) } }
            }
            SettingRow {
                label: tab.tr.fast_model; separator: false
                Dropdown { implicitWidth: 380; options: settings.models; value: tab.c["llm.model_fast"]; onChosen: function(v) { tab.set("llm.model_fast", v) } }
            }
        }
        Card {
            title: tab.tr.sec_behavior
            SettingRow {
                label: tab.tr.thinking; hint: tab.tr.thinking_hint
                Segmented { options: [{value: "off", label: tab.tr.th_off}, {value: "auto", label: tab.tr.th_auto}, {value: "on", label: tab.tr.th_on}]
                            value: tab.c["llm.thinking"]; onChosen: function(v) { tab.set("llm.thinking", v) } }
            }
            SettingRow {
                label: tab.tr.temperature; hint: tab.tr.temperature_hint
                ValueSlider { from: -1; to: 2; step: 0.05; decimals: 2; value: tab.c["llm.temperature"]; onCommitted: function(v) { tab.set("llm.temperature", v) } }
            }
            SettingRow {
                label: tab.tr.max_steps
                ValueSlider { from: 1; to: 40; step: 1; decimals: 0; value: tab.c["llm.max_agent_steps"] || 8; onCommitted: function(v) { tab.set("llm.max_agent_steps", v) } }
            }
            SettingRow {
                label: tab.tr.context
                Dropdown { implicitWidth: 160; options: [4096, 8192, 16384, 32768, 65536].map(function(n) { return {value: n, label: n.toString()} })
                           value: tab.c["llm.context_tokens"]; onChosen: function(v) { tab.set("llm.context_tokens", v) } }
            }
            SettingRow {
                label: tab.tr.vision
                ToggleSwitch { checked: tab.c["llm.vision"] === true; onToggled: function(on) { tab.set("llm.vision", on) } }
            }
            SettingRow {
                label: tab.tr.conv_timeout; separator: false
                ValueSlider { from: 0; to: 60; step: 1; decimals: 0; unit: tab.tr.minutes; value: tab.c["llm.conversation_timeout_min"] || 0
                              onCommitted: function(v) { tab.set("llm.conversation_timeout_min", v) } }
            }
        }
        Card {
            title: tab.tr.sec_perf
            SettingRow {
                label: tab.tr.idle_unload; hint: tab.tr.idle_unload_hint
                ValueSlider { from: 0; to: 120; step: 5; decimals: 0; unit: tab.tr.minutes; value: tab.c["llm.idle_unload_min"] || 0
                              onCommitted: function(v) { tab.set("llm.idle_unload_min", v) } }
            }
            SettingRow {
                label: tab.tr.game_mode_auto; hint: tab.tr.game_mode_hint
                ToggleSwitch { checked: tab.c["performance.game_mode_auto"] === true; onToggled: function(on) { tab.set("performance.game_mode_auto", on) } }
            }
            SettingRow {
                label: tab.tr.eco_qos; separator: false
                ToggleSwitch { checked: tab.c["performance.eco_qos"] === true; onToggled: function(on) { tab.set("performance.eco_qos", on) } }
            }
        }
        Card {
            title: tab.tr.sec_bench
            Text { Layout.fillWidth: true; Layout.topMargin: 4; wrapMode: Text.Wrap; color: tab.th.dim; font.pixelSize: 13
                   text: tab.llm.bench || tab.tr.bench_none }
        }
        Card {
            title: tab.tr.sec_adv
            SettingRow {
                label: tab.tr.server_port
                InputField { implicitWidth: 120; value: String(tab.c["llm.server_port"] || "")
                             onCommitted: function(t) { tab.set("llm.server_port", parseInt(t)) } }
            }
            SettingRow {
                label: tab.tr.server_path
                InputField { value: tab.c["llm.server_path"] || ""; onCommitted: function(t) { tab.set("llm.server_path", t) } }
            }
            SettingRow {
                label: tab.tr.extra_args; separator: false
                InputField { value: (tab.c["llm.extra_args"] || []).join(" ")
                             onCommitted: function(t) { tab.set("llm.extra_args", t.trim().length ? t.trim().split(/\s+/) : []) } }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
