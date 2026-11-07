import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "setup"
import "common"

// The setup wizard: one question at a time. Data and actions come from the `setup` object
// (SetupModel in ui/onboarding.py); each kind of question has its own page in setup/.
ApplicationWindow {
    id: win
    width: 800; height: 660
    minimumWidth: 660; minimumHeight: 560
    visible: false
    title: (setup.ui.setup || "Setup") + " · " + (setup.q.kind === "finish" ? "" : (setup.q.index + " / " + setup.q.total))
    color: th.bg
    font.family: th.font
    property bool allowClose: false
    readonly property var th: look.colors

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
    palette.shadow: "#000000"

    onClosing: function(close) {
        if (!allowClose) { close.accepted = false; setup.requestClose() }
    }

    function pageFor(q) {
        if (!q || !q.kind) return ""
        var map = { "text": "TextPage", "choice": "ChoicePage", "bool": "BoolPage", "number": "NumberPage",
                    "multi": "MultiPage", "form": "FormPage", "matrix": "MatrixPage", "wake": "WakePage",
                    "mic": "MicPage", "folders": "FoldersPage", "hotkeys": "HotkeysPage",
                    "voicelock": "VoiceLockPage", "routines": "RoutinesPage", "finish": "FinishPage" }
        var name = map[q.page] || map[q.kind] || "TextPage"
        return "setup/" + name + ".qml"
    }
    function next() {
        if (setup.atEnd) { setup.done(); return }
        if (page.item && page.item.currentValue !== undefined) setup.submit(page.item.currentValue())
        else setup.skip()
    }

    Shortcut { sequences: ["Ctrl+Return", "Ctrl+Enter"]; onActivated: win.next() }
    Shortcut { sequence: "Alt+Left"; onActivated: setup.back() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ------------------------------------------------------------------ header
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 72
            color: th.bg
            RowLayout {
                anchors { fill: parent; leftMargin: 24; rightMargin: 20 }
                spacing: 14
                Orb {
                    Layout.preferredWidth: 46; Layout.preferredHeight: 46
                    accent: th.accent
                    mode: setup.orbMode
                    level: setup.level
                }
                ColumnLayout {
                    spacing: 2
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: setup.ui.setup || "Setup"
                        color: th.fg
                        font { pixelSize: 15; weight: Font.DemiBold }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: setup.q.kind !== "finish"
                        text: setup.q.index + " " + (setup.ui.of || "of") + " " + setup.q.total
                        color: th.dim
                        font.pixelSize: 12
                    }
                }
                Toggle {
                    text: setup.ui.speak || ""
                    icon: "🔊"
                    checked: setup.speakMode
                    enabled: setup.canSpeak
                    onToggled: function(on) { setup.setSpeakMode(on) }
                }
                Toggle {
                    text: setup.ui.listen || ""
                    icon: "🎤"
                    checked: setup.voiceMode && setup.canListen
                    enabled: setup.canListen
                    onToggled: function(on) { setup.setVoiceMode(on) }
                }
            }
        }
        Rectangle {   // progress
            Layout.fillWidth: true
            Layout.preferredHeight: 3
            color: th.card2
            Rectangle {
                height: parent.height
                width: setup.q.kind === "finish" ? parent.width
                       : parent.width * Math.max(0, (setup.q.index - 1)) / Math.max(1, setup.q.total)
                color: th.accent
                Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            }
        }

        // ------------------------------------------------------------------ question
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                anchors { fill: parent; leftMargin: 40; rightMargin: 40; topMargin: 26; bottomMargin: 10 }
                spacing: 10
                Text {
                    visible: setup.q.kind !== "finish"
                    Layout.fillWidth: true
                    text: setup.q.title || ""
                    color: th.fg
                    wrapMode: Text.Wrap
                    font { pixelSize: 24; weight: Font.DemiBold }
                }
                Text {
                    visible: (setup.q.help || "").length > 0 && setup.q.kind !== "finish"
                    Layout.fillWidth: true
                    text: setup.q.help || ""
                    color: th.dim
                    wrapMode: Text.Wrap
                    font.pixelSize: 14
                    lineHeight: 1.15
                }
                Item { Layout.preferredHeight: 6 }
                Loader {
                    id: page
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    source: win.pageFor(setup.q)
                    onLoaded: { item.q = setup.q; if (item.focusFirst) item.focusFirst() }
                    // a new question with the same page type: reload so the page starts fresh
                    Connections {
                        target: setup
                        function onQChanged() { page.source = ""; page.source = win.pageFor(setup.q) }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: setup.status.length > 0
                    text: setup.status
                    color: setup.statusKind === "error" ? th.danger : th.dim
                    wrapMode: Text.Wrap
                    font { pixelSize: 13; italic: setup.statusKind !== "error" }
                }
            }
        }

        // ------------------------------------------------------------------ footer
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: setup.download.length > 0 ? 112 : 76
            color: th.card
            Rectangle { width: parent.width; height: 1; color: th.border }
            ColumnLayout {
                anchors { fill: parent; leftMargin: 24; rightMargin: 24; topMargin: 12; bottomMargin: 14 }
                spacing: 10
                RowLayout {
                    visible: setup.download.length > 0
                    Layout.fillWidth: true
                    spacing: 10
                    Text { text: setup.downloadActive ? "⬇" : "✓"; color: setup.downloadActive ? th.accent : th.ok; font.pixelSize: 13 }
                    Text {
                        Layout.fillWidth: true
                        text: setup.download
                        color: th.dim
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        font.pixelSize: 12
                    }
                    Rectangle {
                        visible: setup.downloadActive
                        Layout.preferredWidth: 160; Layout.preferredHeight: 6
                        radius: 3
                        color: th.card2
                        Rectangle {
                            width: parent.width * Math.min(1, setup.downloadPct / 100)
                            height: parent.height; radius: 3
                            color: th.accent
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Btn {
                        text: setup.ui.back || "Back"
                        visible: setup.q.canBack === true || setup.atEnd
                        onClicked: setup.back()
                    }
                    Btn {
                        text: "↻ " + (setup.ui.repeat || "Repeat")
                        visible: !setup.atEnd && setup.canSpeak && setup.speakMode
                        onClicked: setup.repeat()
                    }
                    Item { Layout.fillWidth: true }
                    Btn {
                        text: setup.ui.skip || "Skip"
                        visible: !setup.atEnd
                        onClicked: setup.skip()
                    }
                    Btn {
                        primary: true
                        text: setup.atEnd ? (setup.ui.finish || "Finish") : (setup.ui.next || "Next")
                        onClicked: win.next()
                    }
                }
            }
        }
    }
}
