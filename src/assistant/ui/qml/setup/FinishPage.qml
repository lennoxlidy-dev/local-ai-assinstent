import QtQuick
import "../common"
import QtQuick.Layouts
import ".."

Item {
    id: page
    property var q: ({})
    readonly property var th: look.colors
    readonly property var info: setup.finish || ({})
    ColumnLayout {
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width, 560)
        spacing: 14
        Orb {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 96; Layout.preferredHeight: 96
            accent: page.th.accent
            mode: "done"
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: page.info.title || ""
            color: page.th.fg
            font { pixelSize: 28; weight: Font.DemiBold }
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: page.info.text || ""
            color: page.th.dim
            font.pixelSize: 15
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: tips.implicitHeight + 28
            radius: 12
            color: page.th.card
            border.width: 1
            border.color: page.th.border
            ColumnLayout {
                id: tips
                anchors { fill: parent; margins: 14 }
                spacing: 6
                Repeater {
                    model: page.info.tips || []
                    Text { required property string modelData; text: "•  " + modelData; color: page.th.fg; font.pixelSize: 14 }
                }
            }
        }
    }
}
