import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Slider with the value next to it; reports the value when released (or changed with the keyboard).
RowLayout {
    id: vs
    property real from: 0
    property real to: 1
    property real step: 0.1
    property real value: 0
    property string unit: ""
    property int decimals: 1
    signal committed(real value)
    readonly property var th: look.colors
    spacing: 10
    function snap(v) { return Math.round(Math.round(v / step) * step * 1000) / 1000 }
    Slider {
        id: s
        Layout.preferredWidth: 200
        from: vs.from; to: vs.to; stepSize: vs.step
        value: vs.value
        onPressedChanged: if (!pressed && vs.snap(value) !== vs.value) vs.committed(vs.snap(value))
        onMoved: if (!pressed) vs.committed(vs.snap(value))
        background: Rectangle {
            x: s.leftPadding; y: s.topPadding + s.availableHeight / 2 - height / 2
            width: s.availableWidth; height: 4; radius: 2; color: vs.th.card2
            Rectangle { width: s.visualPosition * parent.width; height: parent.height; radius: 2; color: vs.th.accent }
        }
        handle: Rectangle {
            x: s.leftPadding + s.visualPosition * (s.availableWidth - width)
            y: s.topPadding + s.availableHeight / 2 - height / 2
            width: 18; height: 18; radius: 9; color: "white"; border.width: 3; border.color: vs.th.accent
        }
    }
    Text { text: s.value.toFixed(vs.decimals) + vs.unit; color: vs.th.fg; font.pixelSize: 13; Layout.preferredWidth: 70 }
}
