import QtQuick
import QtQuick.Layouts

// A settings section: title, optional hint, then rows.
Rectangle {
    id: card
    property string title: ""
    property string hint: ""
    default property alias content: body.data
    readonly property var th: look.colors
    Layout.fillWidth: true
    implicitHeight: col.implicitHeight + 30
    radius: 12
    color: th.card
    border.width: 1
    border.color: th.border
    ColumnLayout {
        id: col
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 16; topMargin: 14 }
        spacing: 6
        Text { visible: card.title !== ""; text: card.title; color: card.th.fg; Layout.fillWidth: true
               font { pixelSize: 15; weight: Font.DemiBold } }
        Text { visible: card.hint !== ""; text: card.hint; color: card.th.dim; font.pixelSize: 12
               wrapMode: Text.Wrap; Layout.fillWidth: true }
        ColumnLayout { id: body; Layout.fillWidth: true; spacing: 0 }
    }
}
