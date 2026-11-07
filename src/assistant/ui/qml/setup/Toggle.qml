import QtQuick

// Pill toggle with an icon, used in the header (read aloud / answer by voice).
Rectangle {
    id: t
    property string text: ""
    property string icon: ""
    property bool checked: false
    signal toggled(bool on)
    readonly property var th: setup.theme
    implicitHeight: 32
    implicitWidth: row.implicitWidth + 22
    radius: 16
    opacity: enabled ? 1 : 0.4
    color: checked ? Qt.rgba(Qt.color(th.accent).r, Qt.color(th.accent).g, Qt.color(th.accent).b, 0.18) : th.card2
    border.width: 1
    border.color: checked ? th.accent : th.border
    Row {
        id: row
        anchors.centerIn: parent
        spacing: 6
        Text { text: t.icon; font.pixelSize: 13; color: t.th.fg; anchors.verticalCenter: parent.verticalCenter }
        Text { text: t.text; font.pixelSize: 12; color: t.checked ? t.th.fg : t.th.dim; anchors.verticalCenter: parent.verticalCenter }
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: if (t.enabled) t.toggled(!t.checked)
    }
}
