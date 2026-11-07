import QtQuick

// Windows 11 style on/off switch. The owner sets `checked` from the setting and handles `toggled`.
Rectangle {
    id: sw
    property bool checked: false
    signal toggled(bool on)
    readonly property var th: look.colors
    width: 44; height: 22; radius: 11
    opacity: enabled ? 1 : 0.4
    color: checked ? (ma.containsMouse ? Qt.lighter(th.accent, 1.1) : th.accent) : (ma.containsMouse ? th.hover : "transparent")
    border.width: checked ? 0 : 1.5
    border.color: th.dim
    activeFocusOnTab: true
    Keys.onSpacePressed: if (enabled) sw.toggled(!sw.checked)
    Rectangle {
        width: 12; height: 12; radius: 6
        y: 5
        x: sw.checked ? sw.width - width - 5 : 5
        color: sw.checked ? "white" : sw.th.dim
        Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }
    Rectangle { anchors.fill: parent; anchors.margins: -3; radius: 14; color: "transparent"; border.width: 2
                border.color: sw.th.accent; visible: sw.activeFocus }
    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: if (sw.enabled) sw.toggled(!sw.checked) }
}
