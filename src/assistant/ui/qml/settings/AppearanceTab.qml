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
    readonly property var swatches: ["#7C5CFF", "#3B82F6", "#06B6D4", "#22C55E", "#EAB308", "#F97316", "#EF4444", "#EC4899"]
    function set(k, v) { settings.call("set", {key: k, value: v}) }
    ColumnLayout {
        width: tab.availableWidth - 14
        spacing: 14
        Card {
            title: tab.tr.sec_colors
            SettingRow {
                label: tab.tr.theme
                Segmented { options: [{value: "dark", label: tab.tr.th_dark}, {value: "light", label: tab.tr.th_light}, {value: "system", label: tab.tr.th_system}]
                            value: tab.c["appearance.theme"]; onChosen: function(v) { tab.set("appearance.theme", v) } }
            }
            SettingRow {
                label: tab.tr.accent; separator: false
                Row {
                    spacing: 8
                    Repeater {
                        model: tab.swatches
                        Rectangle {
                            required property string modelData
                            readonly property bool on: (tab.c["appearance.accent"] || "").toUpperCase() === modelData
                            width: 26; height: 26; radius: 13
                            color: modelData
                            border.width: on ? 3 : 0
                            border.color: tab.th.fg
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: tab.set("appearance.accent", modelData) }
                        }
                    }
                    InputField { implicitWidth: 96; value: tab.c["appearance.accent"] || ""; onCommitted: function(t) { tab.set("appearance.accent", t.trim()) } }
                }
            }
        }
        Card {
            title: tab.tr.sec_popup
            SettingRow {
                label: tab.tr.popup_position
                Grid {   // where on the screen: a tiny monitor with six spots
                    columns: 3; spacing: 6
                    Repeater {
                        model: ["top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"]
                        Rectangle {
                            required property string modelData
                            readonly property bool on: tab.c["appearance.popup_position"] === modelData
                            width: 64; height: 34; radius: 6
                            color: on ? tab.th.accent : (ma.containsMouse ? tab.th.hover : tab.th.card2)
                            border.width: 1; border.color: on ? tab.th.accent : tab.th.border
                            Rectangle { width: 22; height: 6; radius: 3; color: parent.on ? "white" : tab.th.dim
                                        x: modelData.endsWith("left") ? 6 : modelData.endsWith("right") ? parent.width - width - 6 : (parent.width - width) / 2
                                        y: modelData.startsWith("top") ? 6 : parent.height - height - 6 }
                            MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: tab.set("appearance.popup_position", modelData) }
                            ToolTip.visible: ma.containsMouse
                            ToolTip.text: tab.tr["pos_" + modelData] || modelData
                        }
                    }
                }
            }
            SettingRow {
                label: tab.tr.popup_timeout
                ValueSlider { from: 1; to: 30; step: 1; decimals: 0; unit: tab.tr.seconds; value: tab.c["appearance.popup_timeout_s"] || 6
                              onCommitted: function(v) { tab.set("appearance.popup_timeout_s", v) } }
            }
            SettingRow {
                label: tab.tr.popup_blur
                ToggleSwitch { checked: tab.c["appearance.popup_blur"] === true; onToggled: function(on) { tab.set("appearance.popup_blur", on) } }
            }
            SettingRow {
                label: tab.tr.test_popup; separator: false
                Btn { small: true; text: tab.tr.test; onClicked: settings.call("testPopup", {}) }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
