import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var sel: (q.value || []).slice()
    function currentValue() { return sel }
    function toggle(v) {
        var s = sel.slice(); var i = s.indexOf(v)
        if (i >= 0) s.splice(i, 1); else s.push(v)
        sel = s
    }
    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ColumnLayout {
            width: Math.min(parent.width, 600)
            spacing: 8
            Repeater {
                model: page.q.options || []
                OptionRow {
                    required property var modelData
                    multi: true
                    label: modelData.label
                    hint: modelData.hint || ""
                    selected: page.sel.indexOf(modelData.value) >= 0
                    onPicked: page.toggle(modelData.value)
                }
            }
        }
    }
}
