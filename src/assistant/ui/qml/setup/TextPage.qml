import QtQuick
import "../common"
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    readonly property var th: look.colors
    function currentValue() { return field.text.trim() }
    function focusFirst() { field.forceActiveFocus(); field.selectAll() }
    ColumnLayout {
        width: Math.min(parent.width, 520)
        TextField {
            id: field
            Layout.fillWidth: true
            text: page.q.value !== undefined && page.q.value !== null ? String(page.q.value) : ""
            font.pixelSize: 18
            color: page.th.fg
            selectByMouse: true
            padding: 12
            background: Rectangle {
                radius: 10
                color: page.th.field
                border.width: field.activeFocus ? 2 : 1
                border.color: field.activeFocus ? page.th.accent : page.th.border
            }
            onAccepted: setup.submit(field.text.trim())
        }
    }
}
