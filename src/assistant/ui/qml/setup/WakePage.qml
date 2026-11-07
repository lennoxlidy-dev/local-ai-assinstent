import QtQuick
import "../common"
import QtQuick.Layouts
import ".."

// Say the name three times: beep -> record -> ... -> calibration result.
Item {
    id: page
    property var q: ({})
    readonly property var th: look.colors
    readonly property var st: setup.pageState || ({})
    readonly property bool busy: st.step === "starting" || st.step === "recording" || st.step === "processing"
    ColumnLayout {
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width, 520)
        spacing: 16
        Orb {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 110; Layout.preferredHeight: 110
            accent: page.th.accent
            mode: page.st.step === "recording" ? "listening" : page.st.step === "processing" ? "thinking"
                  : page.st.step === "done" ? (page.st.ok ? "done" : "error") : "idle"
            level: setup.level
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: (setup.ui.say_name || "Say “{name}”").replace("{name}", page.q.name || "")
            color: page.th.fg
            font { pixelSize: 22; weight: Font.DemiBold }
        }
        Row {
            Layout.alignment: Qt.AlignHCenter
            spacing: 12
            Repeater {
                model: 3
                Rectangle {
                    required property int index
                    readonly property int n: page.st.n || 0
                    width: 14; height: 14; radius: 7
                    color: page.st.step === "done" || index + 1 < n || (index + 1 === n && page.st.step !== "recording")
                           ? page.th.accent : (index + 1 === n ? page.th.warn : page.th.card2)
                    border.width: 1
                    border.color: page.th.border
                }
            }
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            color: page.st.step === "done" ? (page.st.ok ? page.th.ok : page.th.warn) : page.th.dim
            font.pixelSize: 14
            text: page.st.step === "recording" ? (setup.ui.recording || "").replace("{n}", page.st.n).replace("{total}", page.st.total)
                : page.st.step === "processing" ? (setup.ui.processing || "")
                : page.st.step === "done" ? ((setup.ui.detected || "").replace("{detected}", page.st.detected)
                                             .replace("{total}", page.st.total) + " - " + page.st.text)
                : ""
        }
        Text {
            visible: (page.st.variants || "").length > 0
            Layout.alignment: Qt.AlignHCenter
            text: page.st.variants || ""
            color: page.th.dim
            font.pixelSize: 12
        }
        Btn {
            Layout.alignment: Qt.AlignHCenter
            primary: page.st.step !== "done"
            enabled: !page.busy
            text: page.st.step === "done" ? (setup.ui.again || "Try again") : (setup.ui.start || "Start")
            onClicked: setup.startWake()
        }
    }
}
