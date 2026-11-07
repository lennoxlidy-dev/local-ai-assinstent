import QtQuick
import QtQuick.Controls.Basic

// Text field that reports a change when editing is finished (Enter / focus out).
TextField {
    id: f
    property string value: ""
    signal committed(string text)
    readonly property var th: look.colors
    text: value
    onValueChanged: if (!activeFocus) { text = value; cursorPosition = 0 }
    Component.onCompleted: cursorPosition = 0
    onEditingFinished: if (text !== value) committed(text)
    implicitWidth: 280
    color: th.fg
    placeholderTextColor: th.dim
    selectByMouse: true
    padding: 8
    font.pixelSize: 13
    background: Rectangle { radius: 8; color: f.th.field; border.width: f.activeFocus ? 2 : 1
                            border.color: f.activeFocus ? f.th.accent : f.th.border }
}
