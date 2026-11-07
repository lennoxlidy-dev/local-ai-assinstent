import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var vals: Object.assign({}, q.value || {})
    readonly property var th: setup.theme
    function currentValue() { return vals }
    function put(k, v) { var o = Object.assign({}, vals); o[k] = v; vals = o }
    function focusFirst() { if (rep.count > 0 && rep.itemAt(0)) rep.itemAt(0).focusInput() }
    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ColumnLayout {
            width: Math.min(parent.width, 600)
            spacing: 14
            Repeater {
                id: rep
                model: page.q.fields || []
                ColumnLayout {
                    id: fieldBox
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 6
                    function focusInput() { if (modelData.multiline) area.forceActiveFocus(); else line.forceActiveFocus() }
                    Text { text: modelData.label; color: page.th.fg; font { pixelSize: 13; weight: Font.DemiBold } }
                    TextField {
                        id: line
                        visible: !modelData.multiline
                        Layout.fillWidth: true
                        text: page.vals[modelData.key] || ""
                        placeholderText: modelData.placeholder || ""
                        maximumLength: modelData.maxLen || 200
                        color: page.th.fg
                        padding: 10
                        font.pixelSize: 14
                        background: Rectangle { radius: 10; color: page.th.field; border.width: line.activeFocus ? 2 : 1
                                                border.color: line.activeFocus ? page.th.accent : page.th.border }
                        onTextEdited: page.put(modelData.key, text)
                        onAccepted: setup.submit(page.vals)
                    }
                    Rectangle {
                        visible: modelData.multiline === true
                        Layout.fillWidth: true
                        Layout.preferredHeight: 92
                        radius: 10
                        color: page.th.field
                        border.width: area.activeFocus ? 2 : 1
                        border.color: area.activeFocus ? page.th.accent : page.th.border
                        TextArea {
                            id: area
                            anchors.fill: parent
                            text: page.vals[modelData.key] || ""
                            placeholderText: modelData.placeholder || ""
                            wrapMode: TextArea.Wrap
                            color: page.th.fg
                            font.pixelSize: 14
                            padding: 10
                            background: null
                            onTextChanged: if (activeFocus) page.put(modelData.key, text.substring(0, modelData.maxLen || 400))
                        }
                    }
                    Flow {
                        visible: (modelData.suggestions || []).length > 0
                        Layout.fillWidth: true
                        spacing: 6
                        Repeater {
                            model: modelData.suggestions || []
                            Rectangle {
                                required property string modelData
                                readonly property bool on: page.vals[fieldBox.modelData.key] === modelData
                                height: 26; radius: 13
                                width: chip.implicitWidth + 20
                                color: on ? page.th.accent : page.th.card2
                                border.width: 1
                                border.color: on ? page.th.accent : page.th.border
                                Text { id: chip; anchors.centerIn: parent; text: modelData; color: on ? "white" : page.th.fg; font.pixelSize: 12 }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: { var v = on ? "" : modelData; page.put(fieldBox.modelData.key, v); line.text = v }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
