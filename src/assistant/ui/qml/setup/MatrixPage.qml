import QtQuick
import "../common"
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var vals: Object.assign({}, q.value || {})
    readonly property var th: look.colors
    function currentValue() { return vals }
    ListView {
        anchors.fill: parent
        clip: true
        spacing: 6
        model: page.q.rows || []
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        delegate: Rectangle {
            required property var modelData
            width: ListView.view.width - 12
            height: Math.max(48, info.implicitHeight + 16)
            radius: 10
            color: page.th.card
            border.width: 1
            border.color: page.th.border
            RowLayout {
                anchors { fill: parent; leftMargin: 14; rightMargin: 10 }
                spacing: 10
                ColumnLayout {
                    id: info
                    Layout.fillWidth: true
                    spacing: 1
                    Text { text: modelData.label; color: page.th.fg; font.pixelSize: 14; Layout.fillWidth: true; elide: Text.ElideRight }
                    Text { visible: (modelData.hint || "").length > 0; text: modelData.hint || ""; color: page.th.dim
                           font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                }
                Segmented {
                    options: modelData.options
                    value: page.vals[modelData.key]
                    onChosen: function(v) { var o = Object.assign({}, page.vals); o[modelData.key] = v; page.vals = o }
                }
            }
        }
    }
}
