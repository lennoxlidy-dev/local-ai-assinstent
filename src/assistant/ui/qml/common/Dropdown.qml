import QtQuick
import QtQuick.Controls.Basic

// Choice from [{value, label}]; `value` is the current setting, `chosen` reports a new one.
ComboBox {
    id: c
    property var options: []
    property var value
    signal chosen(var value)
    readonly property var th: look.colors
    function indexFor(v) { for (var i = 0; i < options.length; i++) if (options[i].value === v) return i; return -1 }
    model: options.map(function(o) { return o.label })
    currentIndex: indexFor(value)
    displayText: currentIndex >= 0 ? options[currentIndex].label : (value === undefined || value === null ? "" : String(value))
    onActivated: function(i) { if (options[i].value !== value) c.chosen(options[i].value) }
    implicitWidth: 240
    font.pixelSize: 13
    background: Rectangle { radius: 8; color: c.th.field; border.width: c.activeFocus ? 2 : 1
                            border.color: c.activeFocus ? c.th.accent : c.th.border }
}
