import QtQuick
import QtQuick.Layouts

// A selectable card: radio or check mark, label, hint, optional preview button.
Rectangle {
    id: o
    property string label: ""
    property string hint: ""
    property bool selected: false
    property bool multi: false
    property bool preview: false
    property string previewText: "Play"
    signal picked()
    signal doublePicked()
    signal previewed()
    readonly property var th: setup.theme
    Layout.fillWidth: true
    implicitHeight: Math.max(50, col.implicitHeight + 20)
    radius: 11
    color: selected ? Qt.rgba(Qt.color(th.accent).r, Qt.color(th.accent).g, Qt.color(th.accent).b, 0.16)
                    : (ma.containsMouse ? th.card2 : th.card)
    border.width: selected ? 2 : 1
    border.color: selected ? th.accent : th.border
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: o.picked()
        onDoubleClicked: o.doublePicked()
    }
    RowLayout {
        anchors { fill: parent; leftMargin: 14; rightMargin: 12 }
        spacing: 12
        Rectangle {   // radio / checkbox
            Layout.preferredWidth: 18; Layout.preferredHeight: 18
            radius: o.multi ? 5 : 9
            color: o.selected ? o.th.accent : "transparent"
            border.width: 2
            border.color: o.selected ? o.th.accent : o.th.dim
            Text {
                anchors.centerIn: parent
                visible: o.selected
                text: o.multi ? "✓" : ""
                color: "white"
                font { pixelSize: 12; bold: true }
            }
            Rectangle {
                visible: o.selected && !o.multi
                anchors.centerIn: parent
                width: 7; height: 7; radius: 4
                color: "white"
            }
        }
        ColumnLayout {
            id: col
            Layout.fillWidth: true
            spacing: 2
            Text { text: o.label; color: o.th.fg; font.pixelSize: 15; Layout.fillWidth: true; wrapMode: Text.Wrap }
            Text { visible: o.hint.length > 0; text: o.hint; color: o.th.dim; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.Wrap }
        }
        Btn {
            visible: o.preview
            small: true
            text: "▶ " + o.previewText
            onClicked: o.previewed()
        }
    }
}
