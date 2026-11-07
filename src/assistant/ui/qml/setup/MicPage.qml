import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property string device: q.value || ""
    readonly property var th: setup.theme
    readonly property var st: setup.pageState || ({})
    function currentValue() { return device }
    ColumnLayout {
        width: Math.min(parent.width, 540)
        spacing: 16
        ComboBox {
            id: combo
            Layout.fillWidth: true
            model: (page.q.devices || [""]).map(function(d) { return d === "" ? (setup.ui.default_mic || "Windows default") : d })
            currentIndex: Math.max(0, (page.q.devices || [""]).indexOf(page.device))
            onActivated: function(i) { page.device = page.q.devices[i]; setup.selectMic(page.device) }
            font.pixelSize: 14
        }
        Text { text: setup.ui.level || "Microphone level"; color: page.th.dim; font.pixelSize: 12 }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 18
            radius: 9
            color: page.th.card2
            Rectangle {   // live level (rms, scaled)
                height: parent.height; radius: 9
                width: parent.width * Math.min(1, Math.sqrt(setup.level * 12))
                color: page.st.verdict === "loud" ? page.th.danger : page.st.verdict === "quiet" ? page.th.warn : page.th.ok
                Behavior on width { NumberAnimation { duration: 60 } }
            }
            Rectangle {   // peak marker
                visible: (page.st.peak || 0) > 0.005
                x: parent.width * Math.min(1, Math.sqrt((page.st.peak || 0))) - 2
                width: 3; height: parent.height
                color: page.th.fg
            }
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: page.st.text || ""
            color: page.st.verdict === "ok" ? page.th.ok : page.st.verdict ? page.th.warn : page.th.dim
            font.pixelSize: 14
        }
    }
}
