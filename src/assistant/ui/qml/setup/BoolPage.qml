import QtQuick
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var sel: q.value
    function currentValue() { return sel === true }
    ColumnLayout {
        width: Math.min(parent.width, 420)
        spacing: 10
        OptionRow {
            label: setup.ui.yes || "Yes"
            selected: page.sel === true
            onPicked: { page.sel = true; setup.submit(true) }
        }
        OptionRow {
            label: setup.ui.no || "No"
            selected: page.sel === false
            onPicked: { page.sel = false; setup.submit(false) }
        }
    }
}
