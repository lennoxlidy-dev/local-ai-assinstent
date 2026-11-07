import QtQuick
import QtQuick.Layouts

// Click a field, then press the new key combination (global hotkeys are paused meanwhile).
Item {
    id: page
    property var q: ({})
    property var vals: Object.assign({}, q.value || {})
    readonly property var th: setup.theme
    function currentValue() { return vals }
    function put(k, v) { var o = Object.assign({}, vals); o[k] = v; vals = o }
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
        m[Qt.Key_QuoteLeft] = "`"; m[Qt.Key_Minus] = "-"; m[Qt.Key_Equal] = "="; m[Qt.Key_Comma] = ","
        m[Qt.Key_Period] = "."; m[Qt.Key_Slash] = "/"; m[Qt.Key_Semicolon] = ";"
        return m[key] || ""
    }
    component KeyField: Rectangle {
        id: kf
        property string key: ""
        property string title: ""
        property bool capturing: activeFocus
        Layout.fillWidth: true
        height: 64
        radius: 10
        color: page.th.card
        border.width: capturing ? 2 : 1
        border.color: capturing ? page.th.accent : page.th.border
        activeFocusOnTab: true
        Keys.onPressed: function(ev) {
            if (ev.key === Qt.Key_Control || ev.key === Qt.Key_Alt || ev.key === Qt.Key_Shift || ev.key === Qt.Key_Meta
                || ev.key === Qt.Key_AltGr) { ev.accepted = true; return }
            if (ev.key === Qt.Key_Escape && ev.modifiers === Qt.NoModifier) { kf.focus = false; ev.accepted = true; return }
            var mods = []
            if (ev.modifiers & Qt.ControlModifier) mods.push("ctrl")
            if (ev.modifiers & Qt.AltModifier) mods.push("alt")
            if (ev.modifiers & Qt.ShiftModifier) mods.push("shift")
            if (ev.modifiers & Qt.MetaModifier) mods.push("win")
            var name = page.keyName(ev.key)
            if (name) { page.put(kf.key, mods.concat([name]).join("+")); kf.focus = false }
            ev.accepted = true
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: kf.forceActiveFocus() }
        RowLayout {
            anchors { fill: parent; leftMargin: 14; rightMargin: 10 }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { Layout.fillWidth: true; text: kf.title; color: page.th.dim; font.pixelSize: 12 }
                Text {
                    Layout.fillWidth: true
                    text: kf.capturing ? (setup.ui.press_keys || "Press the keys…")
                          : ((page.vals[kf.key] || "—").split("+").map(function(p) { return p.length <= 1 ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1) }).join(" + "))
                    color: kf.capturing ? page.th.accent : page.th.fg
                    font { pixelSize: 17; weight: Font.DemiBold }
                }
            }
            Btn { small: true; text: setup.ui.change || "Change"; onClicked: kf.forceActiveFocus() }
            Btn { small: true; visible: kf.key === "push_to_talk"; text: setup.ui.clear || "Clear"; onClicked: page.put(kf.key, "") }
        }
    }
    ColumnLayout {
        width: Math.min(parent.width, 560)
        spacing: 12
        KeyField { key: "push_to_talk"; title: setup.ui.ptt || "Push-to-talk" }
        KeyField { key: "panic"; title: setup.ui.panic || "Panic" }
        OptionRow { label: setup.ui.press || "Press once"; selected: page.vals.mode !== "hold"; onPicked: page.put("mode", "press") }
        OptionRow { label: setup.ui.hold || "Hold while talking"; selected: page.vals.mode === "hold"; onPicked: page.put("mode", "hold") }
    }
}
