import QtQuick
import "../common"
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var sel: q.value
    property string other: ""
    readonly property var th: look.colors
    readonly property bool isOther: page.q.allowOther === true && other.length > 0
    function currentValue() { return isOther ? other : sel }
    function known(v) { var o = page.q.options || []; for (var i = 0; i < o.length; i++) if (o[i].value === v) return true; return false }
    Component.onCompleted: if (page.q.allowOther && page.q.value && !known(page.q.value)) other = String(page.q.value)
    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ColumnLayout {
            width: Math.min(parent.width, 560)
            spacing: 8
            Repeater {
                model: page.q.options || []
                OptionRow {
                    required property var modelData
                    label: modelData.label
                    hint: modelData.hint || ""
                    selected: !page.isOther && page.sel === modelData.value
                    preview: page.q.preview === true
                    previewText: setup.ui.preview || "Play"
                    onPicked: { page.other = ""; page.sel = modelData.value; otherField.text = ""; setup.setValue(modelData.value)
                                if (page.q.preview) setup.preview(modelData.value) }
                    onDoublePicked: setup.submit(modelData.value)
                    onPreviewed: { page.sel = modelData.value; setup.setValue(modelData.value); setup.preview(modelData.value) }
                }
            }
            TextField {
                id: otherField
                visible: page.q.allowOther === true
                Layout.fillWidth: true
                placeholderText: setup.ui.other || "Other…"
                text: page.other
                color: page.th.fg
                padding: 10
                font.pixelSize: 14
                background: Rectangle {
                    radius: 10
                    color: page.th.field
                    border.width: otherField.activeFocus || page.isOther ? 2 : 1
                    border.color: otherField.activeFocus || page.isOther ? page.th.accent : page.th.border
                }
                onTextEdited: page.other = text.trim()
                onAccepted: if (page.other.length > 0) setup.submit(page.other)
            }
        }
    }
}
