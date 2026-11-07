import QtQuick
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    readonly property var th: setup.theme
    readonly property var st: setup.pageState || ({})
    readonly property bool busy: st.step === "starting" || st.step === "recording" || st.step === "processing"
    ColumnLayout {
        width: Math.min(parent.width, 560)
        spacing: 14
        Text {
            visible: page.q.enrolled === true && !page.st.step
            text: "✓ " + (setup.ui.enrolled || "")
            color: page.th.ok
            font.pixelSize: 14
        }
        Rectangle {
            visible: page.st.step === "recording"
            Layout.fillWidth: true
            Layout.preferredHeight: prompt.implicitHeight + 36
            radius: 12
            color: page.th.card
            border.width: 2
            border.color: page.th.accent
            ColumnLayout {
                id: prompt
                anchors { fill: parent; margins: 18 }
                spacing: 6
                Text { text: (setup.ui.read_aloud || "Read aloud:") + "  " + (setup.ui.recording || "").replace("{n}", page.st.n).replace("{total}", page.st.total)
                       color: page.th.dim; font.pixelSize: 12 }
                Text { text: page.st.prompt || ""; color: page.th.fg; font.pixelSize: 20; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
        }
        Rectangle {
            visible: page.st.step === "recording"
            Layout.fillWidth: true
            Layout.preferredHeight: 8
            radius: 4
            color: page.th.card2
            Rectangle { height: parent.height; radius: 4; width: parent.width * Math.min(1, Math.sqrt(setup.level * 12)); color: page.th.ok }
        }
        Text {
            visible: page.st.step === "processing" || page.st.step === "done"
            text: page.st.step === "done" ? "✓ " + (page.st.text || "") : (setup.ui.processing || "")
            color: page.st.step === "done" ? page.th.ok : page.th.dim
            font.pixelSize: 15
        }
        RowLayout {
            spacing: 10
            Btn { primary: page.st.step !== "done"; enabled: !page.busy; text: setup.ui.set_up || "Set up voice lock"; onClicked: setup.startEnroll() }
            Btn { visible: page.st.step !== "done"; enabled: !page.busy; text: setup.ui.not_now || "Not now"; onClicked: setup.submit(false) }
        }
    }
}
