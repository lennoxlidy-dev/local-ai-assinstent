import QtQuick

// The assistant popup. Compact: orb + status + live transcript + answer (+ permission question).
// Expanded (click): a mini chat with a text box. All data comes from the `popup` bridge object
// (PopupModel in popup.py). The window follows wantW/wantH (Python resizes it in one step).
// Only core Qt Quick types are used (no Controls), so it loads fast and renders in software.
Item {
    id: root
    readonly property int wantW: popup.expanded ? 460 : 420
    readonly property int wantH: popup.expanded ? 580 : Math.min(compact.implicitHeight + 28, 640)
    onWantWChanged: popup.resizeTo(wantW, wantH)
    onWantHChanged: popup.resizeTo(wantW, wantH)
    Component.onCompleted: popup.resizeTo(wantW, wantH)

    opacity: popup.visibleState ? 1.0 : 0.0
    Behavior on opacity { NumberAnimation { duration: popup.visibleState ? 110 : 420; easing.type: Easing.OutCubic } }

    readonly property color bg: popup.dark ? Qt.rgba(0.08, 0.08, 0.10, popup.blur ? 0.70 : 0.96)
                                           : Qt.rgba(0.98, 0.98, 1.0, popup.blur ? 0.74 : 0.97)
    readonly property color fg: popup.dark ? "#F2F3F5" : "#1B1C20"
    readonly property color dim: popup.dark ? "#A3A7B0" : "#5E626B"
    readonly property color soft: popup.dark ? Qt.rgba(1, 1, 1, 0.07) : Qt.rgba(0, 0, 0, 0.05)
    readonly property color warn: popup.dark ? "#F5B44A" : "#B26B00"
    readonly property color accent: popup.accent
    readonly property string mono: Qt.platform.os === "windows" ? "Consolas" : "monospace"

    component Btn: Rectangle {
        id: b
        property string label
        property bool primary: false
        signal clicked()
        implicitWidth: t.implicitWidth + 24
        implicitHeight: 30
        radius: 8
        color: primary ? (ma.containsMouse ? Qt.lighter(root.accent, 1.12) : root.accent)
                       : (ma.containsMouse ? Qt.darker(root.soft, 1.6) : root.soft)
        border.width: primary ? 0 : 1
        border.color: popup.dark ? Qt.rgba(1, 1, 1, 0.12) : Qt.rgba(0, 0, 0, 0.12)
        Text {
            id: t
            anchors.centerIn: parent
            text: b.label
            color: b.primary ? "white" : root.fg
            font.pixelSize: 13
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: b.clicked()
        }
    }

    component ConfirmBox: Column {   // "May I ...?" with the exact command / paths and the choices
        spacing: 6
        Text {
            width: parent.width
            text: popup.confirmTitle
            color: root.fg
            font { pixelSize: 15; weight: Font.DemiBold }
            wrapMode: Text.Wrap
        }
        Flickable {
            width: parent.width
            height: Math.min(details.implicitHeight, 220)
            contentHeight: details.implicitHeight
            clip: true
            interactive: contentHeight > height
            visible: popup.confirmDetails.length > 0
            Text {
                id: details
                width: parent.width
                text: popup.confirmDetails
                color: root.dim
                font { family: root.mono; pixelSize: 12 }
                wrapMode: Text.WrapAnywhere
            }
        }
        Text {
            width: parent.width
            visible: popup.confirmRisk.length > 0
            text: "⚠ " + popup.confirmRisk
            color: root.warn
            font.pixelSize: 12
            wrapMode: Text.Wrap
        }
        Text {
            visible: popup.confirmHint.length > 0
            text: popup.confirmHint
            color: root.dim
            font { pixelSize: 12; italic: true }
        }
        Flow {
            width: parent.width
            spacing: 6
            Repeater {
                model: popup.confirmOptions
                Btn {
                    required property var modelData
                    label: modelData.label
                    primary: modelData.primary === true
                    onClicked: popup.choose(modelData.id)
                }
            }
        }
    }

    Rectangle {
        id: card
        anchors.fill: parent
        radius: popup.radius
        color: root.bg
        border.width: 1
        border.color: popup.dark ? Qt.rgba(1, 1, 1, 0.09) : Qt.rgba(0, 0, 0, 0.09)
        clip: true

        HoverHandler { onHoveredChanged: popup.hover(hovered) }

        MouseArea {   // click anywhere on the compact card -> mini chat
            anchors.fill: parent
            enabled: !popup.expanded && !popup.confirmActive
            cursorShape: Qt.PointingHandCursor
            onClicked: popup.expand()
        }

        // ---------------------------------------------------------------- compact view
        Item {
            id: compact
            visible: !popup.expanded
            x: 14; y: 14
            width: parent.width - 28
            implicitHeight: Math.max(orb.height, textCol.implicitHeight)
                            + (popup.confirmActive ? confirmCompact.implicitHeight + 12 : 0)

            Orb {
                id: orb
                width: 56; height: 56
                accent: root.accent
                mode: popup.state
                level: popup.level
            }

            Column {
                id: textCol
                anchors { left: orb.right; leftMargin: 14; right: parent.right }
                y: Math.max(0, (orb.height - implicitHeight) / 2)   // centred next to the orb while short
                spacing: 4
                Text {
                    text: popup.statusText
                    color: popup.state === "confirm" ? root.warn : root.accent
                    font { pixelSize: 12; weight: Font.DemiBold; letterSpacing: 0.4; capitalization: Font.AllUppercase }
                }
                Text {
                    visible: popup.transcript.length > 0
                    width: parent.width
                    text: popup.transcript
                    color: popup.answer.length > 0 ? root.dim : root.fg
                    font { pixelSize: popup.answer.length > 0 ? 13 : 16; italic: popup.answer.length === 0 }
                    wrapMode: Text.Wrap
                    maximumLineCount: popup.answer.length > 0 ? 2 : 4
                    elide: Text.ElideRight
                }
                Text {
                    visible: popup.progress.length > 0 && popup.answer.length === 0
                    width: parent.width
                    text: popup.progress
                    color: root.dim
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                Text {
                    visible: popup.answer.length > 0
                    width: parent.width
                    text: popup.answer
                    color: root.fg
                    font.pixelSize: 15
                    wrapMode: Text.Wrap
                    maximumLineCount: 7
                    elide: Text.ElideRight
                    lineHeight: 1.08
                }
            }

            ConfirmBox {
                id: confirmCompact
                visible: popup.confirmActive
                width: parent.width
                y: Math.max(orb.height, textCol.implicitHeight) + 12
            }
        }

        // ---------------------------------------------------------------- mini chat
        Item {
            visible: popup.expanded
            anchors.fill: parent
            anchors.margins: 14

            Row {
                id: header
                spacing: 10
                anchors { left: parent.left; top: parent.top }
                Orb { width: 30; height: 30; accent: root.accent; mode: popup.state; level: popup.level }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        text: popup.title
                        color: root.fg
                        font { pixelSize: 15; weight: Font.DemiBold }
                    }
                    Text {
                        text: popup.progress.length > 0 ? popup.progress : popup.statusText
                        color: root.dim
                        font.pixelSize: 11
                        visible: text.length > 0
                    }
                }
            }
            Text {
                text: "✕"
                color: closeMa.containsMouse ? root.fg : root.dim
                font.pixelSize: 16
                anchors { right: parent.right; top: parent.top }
                MouseArea {
                    id: closeMa
                    anchors.fill: parent
                    anchors.margins: -8
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: popup.collapse()
                }
            }

            ListView {
                id: chat
                anchors { left: parent.left; right: parent.right; top: header.bottom; topMargin: 12
                          bottom: confirmChat.visible ? confirmChat.top : inputBox.top; bottomMargin: 10 }
                clip: true
                spacing: 8
                model: popup.messages
                onCountChanged: Qt.callLater(chat.positionViewAtEnd)
                onModelChanged: Qt.callLater(chat.positionViewAtEnd)
                delegate: Item {
                    id: row
                    required property var modelData
                    readonly property bool mine: modelData.role === "user"
                    width: chat.width
                    height: bubble.height
                    Rectangle {
                        id: bubble
                        x: row.mine ? row.width - width : 0
                        width: Math.min(msg.implicitWidth + 24, chat.width * 0.84)
                        height: msg.implicitHeight + 16
                        radius: 12
                        color: row.mine ? root.accent : root.soft
                        Text {
                            id: msg
                            x: 12; y: 8
                            width: bubble.width - 24
                            text: row.modelData.text
                            color: row.mine ? "white" : (row.modelData.ok === false ? root.warn : root.fg)
                            wrapMode: Text.Wrap
                            font.pixelSize: 14
                        }
                    }
                }
                Text {
                    anchors.centerIn: parent
                    visible: chat.count === 0
                    text: popup.emptyText
                    color: root.dim
                    font.pixelSize: 13
                }
            }

            ConfirmBox {
                id: confirmChat
                visible: popup.confirmActive
                anchors { left: parent.left; right: parent.right; bottom: inputBox.top; bottomMargin: 10 }
            }

            Rectangle {
                id: inputBox
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                height: 40
                radius: 10
                color: root.soft
                border.width: 1
                border.color: input.activeFocus ? root.accent : "transparent"
                TextInput {
                    id: input
                    anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                    verticalAlignment: TextInput.AlignVCenter
                    color: root.fg
                    selectionColor: root.accent
                    selectedTextColor: "white"
                    font.pixelSize: 14
                    clip: true
                    selectByMouse: true
                    onAccepted: { if (text.trim().length > 0) { popup.submit(text); text = "" } }
                    Keys.onEscapePressed: popup.collapse()
                }
                Text {
                    anchors.fill: input
                    verticalAlignment: Text.AlignVCenter
                    visible: input.text.length === 0 && !input.inputMethodComposing
                    text: popup.placeholder
                    color: root.dim
                    font.pixelSize: 14
                    elide: Text.ElideRight
                }
            }

            Connections {
                target: popup
                function onExpandedChanged() { if (popup.expanded) input.forceActiveFocus() }
                function onFocusRequested() { if (popup.expanded) input.forceActiveFocus() }
            }
        }
    }
}
