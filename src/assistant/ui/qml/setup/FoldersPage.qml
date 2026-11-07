import QtQuick
import "../common"
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var rules: (q.value || []).slice()
    readonly property var th: look.colors
    function currentValue() { return rules }
    function label(v) { var a = page.q.access || []; for (var i = 0; i < a.length; i++) if (a[i].value === v) return a[i].label; return v }
    function setAccess(i, v) { var r = rules.slice(); r[i] = Object.assign({}, r[i], {access: v}); rules = r }
    function remove(i) { var r = rules.slice(); r.splice(i, 1); rules = r }
    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        Btn {
            text: "+ " + (setup.ui.add_folder || "Add folder…")
            onClicked: {
                var p = setup.browseFolder()
                if (p) { var r = page.rules.slice(); r.unshift({path: p.replace(/\//g, "\\"), access: "full", note: ""}); page.rules = r }
            }
        }
        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            model: page.rules
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Rectangle {
                required property var modelData
                required property int index
                width: ListView.view.width - 12
                height: 48
                radius: 10
                color: page.th.card
                border.width: 1
                border.color: page.th.border
                RowLayout {
                    anchors { fill: parent; leftMargin: 12; rightMargin: 8 }
                    spacing: 8
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Text { text: modelData.path; color: page.th.fg; font.pixelSize: 13; elide: Text.ElideMiddle; Layout.fillWidth: true }
                        Text { visible: (modelData.note || "").length > 0; text: modelData.note || ""; color: page.th.dim; font.pixelSize: 11 }
                    }
                    ComboBox {
                        Layout.preferredWidth: 150
                        model: (page.q.access || []).map(function(a) { return a.label })
                        currentIndex: Math.max(0, (page.q.access || []).map(function(a) { return a.value }).indexOf(modelData.access))
                        onActivated: function(i) { page.setAccess(index, page.q.access[i].value) }
                        font.pixelSize: 12
                    }
                    Btn { small: true; text: "✕"; onClicked: page.remove(index) }
                }
            }
        }
    }
}
