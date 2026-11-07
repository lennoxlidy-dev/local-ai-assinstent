import QtQuick

// Segmented buttons for one matrix row (Allow / Ask / Block, This desktop / Assistant desktop).
Row {
    id: seg
    property var options: []
    property var value
    signal chosen(var value)
    readonly property var th: setup.theme
    spacing: 0
    Repeater {
        model: seg.options
        Rectangle {
            required property var modelData
            required property int index
            readonly property bool on: seg.value === modelData.value
            width: Math.max(74, lbl.implicitWidth + 20)
            height: 30
            color: on ? seg.th.accent : (ma.containsMouse ? seg.th.card2 : seg.th.card)
            border.width: 1
            border.color: on ? seg.th.accent : seg.th.border
            radius: 7
            Text {
                id: lbl
                anchors.centerIn: parent
                text: modelData.label
                color: on ? "white" : seg.th.fg
                font.pixelSize: 12
            }
            MouseArea {
                id: ma
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: { seg.value = modelData.value; seg.chosen(modelData.value) }
            }
        }
    }
}
