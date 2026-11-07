import QtQuick
import QtQuick.Layouts

// One setting: label + hint on the left, its control on the right.
Item {
    id: row
    property string label: ""
    property string hint: ""
    property bool separator: true
    default property alias control: slot.data
    readonly property var th: look.colors
    Layout.fillWidth: true
    implicitHeight: Math.max(46, texts.implicitHeight + 16, slot.childrenRect.height + 12)
    RowLayout {
        anchors.fill: parent
        spacing: 16
        ColumnLayout {
            id: texts
            Layout.fillWidth: true
            spacing: 1
            Text { Layout.fillWidth: true; text: row.label; color: row.th.fg; font.pixelSize: 14; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; visible: row.hint !== ""; text: row.hint; color: row.th.dim; font.pixelSize: 11
                   wrapMode: Text.Wrap }
        }
        Item {
            id: slot
            Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
            implicitWidth: childrenRect.width
            implicitHeight: childrenRect.height
        }
    }
    Rectangle { visible: row.separator; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: row.th.border; opacity: 0.6 }
}
