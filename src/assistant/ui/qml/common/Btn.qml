import QtQuick

// Button: primary (accent) or quiet. Keyboard: Space/Enter when focused.
Rectangle {
    id: b
    property string text: ""
    property bool primary: false
    property bool small: false
    signal clicked()
    readonly property var th: look.colors
    implicitWidth: Math.max(small ? (text.length <= 2 ? 30 : 60) : 88, label.implicitWidth + (small ? 22 : 34))
    implicitHeight: small ? 30 : 38
    radius: 9
    opacity: enabled ? 1.0 : 0.45
    color: primary ? (ma.pressed ? Qt.darker(th.accent, 1.15) : ma.containsMouse ? Qt.lighter(th.accent, 1.1) : th.accent)
                   : (ma.pressed ? Qt.darker(th.card2, 1.2) : ma.containsMouse ? Qt.lighter(th.card2, 1.15) : th.card2)
    border.width: activeFocus ? 2 : (primary ? 0 : 1)
    border.color: activeFocus ? Qt.lighter(th.accent, 1.3) : th.border
    activeFocusOnTab: true
    Keys.onSpacePressed: b.clicked()
    Keys.onReturnPressed: b.clicked()
    Text {
        id: label
        anchors.centerIn: parent
        text: b.text
        color: b.primary ? "white" : b.th.fg
        font { pixelSize: b.small ? 12 : 14; weight: b.primary ? Font.DemiBold : Font.Normal }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: if (b.enabled) b.clicked()
    }
}
