import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "common"

// Settings: a sidebar with ten tabs; only the open tab is loaded. Data and actions: `settings`
// (SettingsModel in ui/settings.py). Every change is saved at once.
ApplicationWindow {
    id: win
    width: 1000; height: 720
    minimumWidth: 820; minimumHeight: 560
    visible: false
    title: (settings.tr.title || "Settings")
    color: th.bg
    font.family: th.font
    readonly property var th: look.colors
    readonly property var tabs: ["general", "voice", "model", "permissions", "apps", "routines", "history",
                                 "privacy", "appearance", "log"]
    readonly property var glyphs: ({general: "", voice: "", model: "", permissions: "",
                                    apps: "", routines: "", history: "", privacy: "",
                                    appearance: "", log: ""})
    readonly property var fallback: ({general: "⚙", voice: "◉", model: "✦", permissions: "⛨", apps: "▦",
                                      routines: "↻", history: "◷", privacy: "⚿", appearance: "◐", log: "☰"})
    readonly property var files: ({general: "General", voice: "Voice", model: "Model", permissions: "Permissions",
                                   apps: "Apps", routines: "Routines", history: "History", privacy: "Privacy",
                                   appearance: "Appearance", log: "Log"})

    palette.window: th.bg
    palette.windowText: th.fg
    palette.base: th.field
    palette.alternateBase: th.card2
    palette.text: th.fg
    palette.button: th.card2
    palette.buttonText: th.fg
    palette.highlight: th.accent
    palette.highlightedText: "white"
    palette.placeholderText: th.dim
    palette.mid: th.border
    palette.dark: th.border
    palette.light: th.card2
    palette.midlight: th.card2

    Shortcut { sequence: "Esc"; onActivated: win.close() }
    Shortcut { sequence: "Ctrl+Tab"; onActivated: settings.call("tab", {name: win.tabs[(win.tabs.indexOf(settings.tab) + 1) % win.tabs.length]}) }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ------------------------------------------------------------------ sidebar
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 256
            color: th.side
            ColumnLayout {
                anchors { fill: parent; topMargin: 18; bottomMargin: 14; leftMargin: 10; rightMargin: 10 }
                spacing: 2
                RowLayout {
                    Layout.leftMargin: 10
                    Layout.bottomMargin: 14
                    spacing: 10
                    Orb { Layout.preferredWidth: 30; Layout.preferredHeight: 30; accent: th.accent; mode: "idle" }
                    Text { text: settings.tr.title || "Settings"; color: th.fg; font { pixelSize: 17; weight: Font.DemiBold } }
                }
                Repeater {
                    model: win.tabs
                    Rectangle {
                        required property string modelData
                        readonly property bool on: settings.tab === modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        radius: 8
                        color: on ? th.card2 : (ma.containsMouse ? th.hover : "transparent")
                        Rectangle { visible: parent.on; width: 3; height: 18; radius: 2; color: th.accent
                                    anchors { left: parent.left; verticalCenter: parent.verticalCenter } }
                        RowLayout {
                            anchors { fill: parent; leftMargin: 14; rightMargin: 8 }
                            spacing: 12
                            Text {
                                Layout.preferredWidth: 18
                                horizontalAlignment: Text.AlignHCenter
                                text: th.iconFont ? win.glyphs[modelData] : win.fallback[modelData]
                                font.family: th.iconFont ? th.icons : th.font
                                font.pixelSize: 15
                                color: parent.parent.on ? th.accent : th.dim
                            }
                            Text {
                                Layout.fillWidth: true
                                text: settings.tr["tab_" + modelData] || modelData
                                color: th.fg
                                elide: Text.ElideRight
                                font { pixelSize: 13; weight: parent.parent.on ? Font.DemiBold : Font.Normal }
                            }
                        }
                        MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                    onClicked: settings.call("tab", {name: modelData}) }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }

        // ------------------------------------------------------------------ content
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            Text {
                Layout.fillWidth: true
                Layout.leftMargin: 32; Layout.topMargin: 22; Layout.bottomMargin: 12
                text: settings.tr["tab_" + settings.tab] || ""
                color: th.fg
                font { pixelSize: 26; weight: Font.DemiBold }
            }
            Loader {
                id: page
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: 32; Layout.rightMargin: 16; Layout.bottomMargin: 12
                source: "settings/" + win.files[settings.tab] + "Tab.qml"
                asynchronous: false
            }
        }
    }

    // ------------------------------------------------------------------ status toast
    Rectangle {
        id: toast
        property int seq: settings.statusSeq
        anchors { right: parent.right; bottom: parent.bottom; margins: 18 }
        width: Math.min(toastText.implicitWidth + 32, parent.width * 0.6)
        height: toastText.implicitHeight + 18
        radius: 10
        color: settings.statusKind === "error" ? th.danger : settings.statusKind === "ok" ? th.card2 : th.card2
        border.width: 1
        border.color: settings.statusKind === "ok" ? th.ok : th.border
        opacity: 0
        visible: opacity > 0.01
        onSeqChanged: { opacity = 1; hide.restart() }
        Behavior on opacity { NumberAnimation { duration: 180 } }
        Timer { id: hide; interval: settings.statusKind === "error" ? 6000 : 2200; onTriggered: toast.opacity = 0 }
        Text {
            id: toastText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, win.width * 0.6 - 32)
            text: (settings.statusKind === "ok" ? "✓  " : "") + settings.status
            color: settings.statusKind === "error" ? "white" : th.fg
            wrapMode: Text.Wrap
            font.pixelSize: 13
        }
    }
}
