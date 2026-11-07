import QtQuick

// A destructive action: the first click arms it ("click again"), the second one runs it.
Btn {
    id: d
    property string confirmText: "Click again to confirm"
    property string normalText: ""
    property bool armed: false
    signal confirmed()
    text: armed ? confirmText : normalText
    Timer { id: disarm; interval: 3500; onTriggered: d.armed = false }
    onClicked: { if (armed) { armed = false; disarm.stop(); d.confirmed() } else { armed = true; disarm.restart() } }
    Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: d.armed ? 2 : 0
                border.color: d.th.danger }
}
