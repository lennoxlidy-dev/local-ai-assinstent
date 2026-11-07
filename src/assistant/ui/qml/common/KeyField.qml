import QtQuick
import QtQuick.Layouts

// Click, then press a key combination. `capturing` lets the owner pause global hotkeys meanwhile.
Rectangle {
    id: kf
    property string value: ""
    property string pressText: "Press the keys…"
    property string changeText: "Change"
    readonly property bool capturing: activeFocus
    signal captured(string spec)
    readonly property var th: look.colors
    implicitWidth: 260
    implicitHeight: 38
    radius: 8
    color: th.field
    border.width: capturing ? 2 : 1
    border.color: capturing ? th.accent : th.border
    activeFocusOnTab: true
    function pretty(spec) {
        if (!spec) return "—"
        return spec.split("+").map(function(p) { return p.length <= 1 ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1) }).join(" + ")
    }
    function keyName(key) {
        if (key >= Qt.Key_A && key <= Qt.Key_Z) return String.fromCharCode(key).toLowerCase()
        if (key >= Qt.Key_0 && key <= Qt.Key_9) return String.fromCharCode(key)
        if (key >= Qt.Key_F1 && key <= Qt.Key_F24) return "f" + (key - Qt.Key_F1 + 1)
        var m = {}
        m[Qt.Key_Space] = "space"; m[Qt.Key_Escape] = "escape"; m[Qt.Key_Return] = "enter"; m[Qt.Key_Enter] = "enter"
        m[Qt.Key_Tab] = "tab"; m[Qt.Key_Backspace] = "backspace"; m[Qt.Key_Insert] = "insert"; m[Qt.Key_Delete] = "delete"
        m[Qt.Key_Home] = "home"; m[Qt.Key_End] = "end"; m[Qt.Key_PageUp] = "pageup"; m[Qt.Key_PageDown] = "pagedown"
        m[Qt.Key_Up] = "up"; m[Qt.Key_Down] = "down"; m[Qt.Key_Left] = "left"; m[Qt.Key_Right] = "right"
        m[Qt.Key_Pause] = "pause"; m[Qt.Key_ScrollLock] = "scrolllock"; m[Qt.Key_Print] = "printscreen"
        return m[key] || ""
    }
    Keys.onPressed: function(ev) {
        ev.accepted = true
        if (ev.key === Qt.Key_Control || ev.key === Qt.Key_Alt || ev.key === Qt.Key_Shift || ev.key === Qt.Key_Meta
            || ev.key === Qt.Key_AltGr) return
        if (ev.key === Qt.Key_Escape && ev.modifiers === Qt.NoModifier) { kf.focus = false; return }
        var mods = []
        if (ev.modifiers & Qt.ControlModifier) mods.push("ctrl")
        if (ev.modifiers & Qt.AltModifier) mods.push("alt")
        if (ev.modifiers & Qt.ShiftModifier) mods.push("shift")
        if (ev.modifiers & Qt.MetaModifier) mods.push("win")
        var name = keyName(ev.key)
        if (name) { kf.focus = false; kf.captured(mods.concat([name]).join("+")) }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: kf.forceActiveFocus() }
    RowLayout {
        anchors { fill: parent; leftMargin: 12; rightMargin: 8 }
        Text { Layout.fillWidth: true; text: kf.capturing ? kf.pressText : kf.pretty(kf.value)
               color: kf.capturing ? kf.th.accent : kf.th.fg; font { pixelSize: 13; weight: Font.DemiBold } }
        Text { text: kf.changeText; color: kf.th.dim; font.pixelSize: 11; visible: !kf.capturing }
    }
}
