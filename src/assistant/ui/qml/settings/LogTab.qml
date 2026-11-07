import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../common"

// Every tool call with the permission decision and the outcome. Click a row for details.
Item {
    id: tab
    readonly property var tr: settings.tr
    readonly property var th: look.colors
    property string kind: "all"
    property int open: -1
    function decisionColor(d) {
        return d === "blocked" || d === "denied" ? tab.th.danger : d === "approved" ? tab.th.warn
             : d === "invalid" || d === "cancelled" ? tab.th.dim : tab.th.ok
    }
    function apply() { settings.call("filterLog", {search: search.text, kind: tab.kind}) }
    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            InputField { id: search; Layout.fillWidth: true; placeholderText: "⌕  " + tab.tr.search_log
                         onTextEdited: delay.restart()
                         Timer { id: delay; interval: 250; onTriggered: tab.apply() } }
            Segmented { options: ["all", "asked", "denied", "blocked", "errors"].map(function(k) { return {value: k, label: tab.tr["f_" + k]} })
                        value: tab.kind; onChosen: function(v) { tab.kind = v; tab.apply() } }
            Btn { small: true; text: "↻"; onClicked: tab.apply() }
            Btn { small: true; text: "⇩ " + tab.tr.export_csv; onClicked: settings.call("exportLog", {fmt: "csv"}) }
            Btn { small: true; text: "⇩ JSON"; onClicked: settings.call("exportLog", {fmt: "json"}) }
        }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 4
            model: settings.actions
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Rectangle {
                required property var modelData
                readonly property bool expanded: tab.open === modelData.id
                width: list.width - 12
                height: row.implicitHeight + (expanded ? details.implicitHeight + 10 : 0) + 16
                radius: 8
                color: expanded ? tab.th.card2 : (ma.containsMouse ? tab.th.hover : tab.th.card)
                border.width: 1
                border.color: tab.th.border
                MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: tab.open = parent.expanded ? -1 : modelData.id }
                ColumnLayout {
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 12 }
                    spacing: 6
                    RowLayout {
                        id: row
                        Layout.fillWidth: true
                        spacing: 10
                        Text { text: modelData.when; color: tab.th.dim; font.pixelSize: 11; Layout.preferredWidth: 104 }
                        Text { text: modelData.summary; color: tab.th.fg; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                        Rectangle {
                            radius: 9; height: 18; width: dtext.implicitWidth + 16
                            color: "transparent"; border.width: 1; border.color: tab.decisionColor(modelData.decision)
                            Text { id: dtext; anchors.centerIn: parent; text: modelData.decisionText; color: tab.decisionColor(modelData.decision); font.pixelSize: 10 }
                        }
                        Text { text: modelData.outcome === "error" ? "✕" : modelData.outcome === "ok" ? "✓" : "·"
                               color: modelData.outcome === "error" ? tab.th.danger : tab.th.dim; font.pixelSize: 13; Layout.preferredWidth: 14 }
                    }
                    ColumnLayout {
                        id: details
                        visible: parent.parent.expanded
                        Layout.fillWidth: true
                        spacing: 3
                        Text { text: modelData.tool + (modelData.source ? "  ·  " + modelData.source : "") + (modelData.ms !== null && modelData.ms !== undefined ? "  ·  " + modelData.ms + " ms" : "")
                               color: tab.th.dim; font.pixelSize: 11 }
                        Text { visible: modelData.risks !== ""; text: "⚠ " + modelData.risks; color: tab.th.warn; font.pixelSize: 11 }
                        Text { text: modelData.args; color: tab.th.fg; font.family: Qt.platform.os === "windows" ? "Consolas" : "monospace"; font.pixelSize: 11
                               wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                        Text { visible: modelData.result !== ""; text: modelData.result; color: tab.th.dim; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
            Text { anchors.centerIn: parent; visible: settings.actions.length === 0; text: tab.tr.no_actions; color: tab.th.dim; font.pixelSize: 13 }
        }
    }
}
