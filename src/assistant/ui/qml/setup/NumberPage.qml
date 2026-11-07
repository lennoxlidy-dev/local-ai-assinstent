import QtQuick
import "../common"
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    readonly property var th: look.colors
    function currentValue() { return Math.round(slider.value * 100) / 100 }
    ColumnLayout {
        width: Math.min(parent.width, 520)
        spacing: 14
        Text {
            text: slider.value.toFixed(1) + " " + (page.q.unit || "")
            color: page.th.fg
            font { pixelSize: 34; weight: Font.DemiBold }
        }
        Slider {
            id: slider
            Layout.fillWidth: true
            from: page.q.min !== undefined ? page.q.min : 0
            to: page.q.max !== undefined ? page.q.max : 1
            stepSize: page.q.step || 0.1
            snapMode: Slider.SnapAlways
            value: page.q.value !== undefined && page.q.value !== null ? page.q.value : from
            background: Rectangle {
                x: slider.leftPadding
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: slider.availableWidth; height: 6; radius: 3
                color: page.th.card2
                Rectangle { width: slider.visualPosition * parent.width; height: parent.height; radius: 3; color: page.th.accent }
            }
            handle: Rectangle {
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: 22; height: 22; radius: 11
                color: "white"
                border.width: 3
                border.color: page.th.accent
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: slider.from.toFixed(1); color: page.th.dim; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            Text { text: slider.to.toFixed(1); color: page.th.dim; font.pixelSize: 12 }
        }
    }
}
