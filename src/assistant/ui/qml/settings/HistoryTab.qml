import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../common"

// Left: search + conversations. Right: the selected conversation (rename, export, delete).
Item {
    id: tab
    readonly property var tr: settings.tr
    readonly property var th: look.colors
    readonly property var conv: settings.conversation || ({})
    property bool renaming: false
    RowLayout {
        anchors.fill: parent
        spacing: 16
        ColumnLayout {
            Layout.preferredWidth: 300
            Layout.maximumWidth: 300
            Layout.fillWidth: false
            Layout.fillHeight: true
            spacing: 8
            InputField {
                id: search
                Layout.fillWidth: true
                placeholderText: "⌕  " + tab.tr.search_conv
                onTextEdited: searchDelay.restart()
                Timer { id: searchDelay; interval: 250; onTriggered: settings.call("searchHistory", {query: search.text}) }
            }
            Text { visible: settings.cfg["privacy.save_conversations"] === false; text: tab.tr.conv_saving_off; color: tab.th.warn
                   font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 4
                model: settings.conversations
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width - 8
                    height: col.implicitHeight + 16
                    radius: 8
                    readonly property bool on: tab.conv.id === modelData.id
                    color: on ? tab.th.card2 : (ma.containsMouse ? tab.th.hover : "transparent")
                    border.width: on ? 1 : 0
                    border.color: tab.th.accent
                    ColumnLayout { id: col; anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
                        spacing: 2
                        RowLayout { Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: modelData.title; color: tab.th.fg; elide: Text.ElideRight; font { pixelSize: 13; weight: Font.DemiBold } }
                            Text { text: modelData.when; color: tab.th.dim; font.pixelSize: 11 } }
                        Text { visible: modelData.snippet !== ""; Layout.fillWidth: true; text: modelData.snippet; color: tab.th.dim
                               font.pixelSize: 11; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight } }
                    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: { tab.renaming = false; settings.call("openConversation", {id: modelData.id}) } }
                }
                Text { anchors.centerIn: parent; visible: settings.conversations.length === 0; text: tab.tr.no_conv; color: tab.th.dim; font.pixelSize: 13 }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 12
            color: tab.th.card
            border.width: 1
            border.color: tab.th.border
            Text { anchors.centerIn: parent; visible: tab.conv.id === undefined; text: tab.tr.pick_conv; color: tab.th.dim; font.pixelSize: 13 }
            ColumnLayout {
                visible: tab.conv.id !== undefined
                anchors { fill: parent; margins: 14 }
                spacing: 10
                Text { visible: !tab.renaming; Layout.fillWidth: true; text: tab.conv.title || ""; color: tab.th.fg
                       wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                       font { pixelSize: 16; weight: Font.DemiBold } }
                InputField { id: titleField; visible: tab.renaming; Layout.fillWidth: true; value: tab.conv.title || ""
                             onAccepted: { settings.call("renameConversation", {id: tab.conv.id, title: text}); tab.renaming = false } }
                Text { text: tab.conv.when || ""; color: tab.th.dim; font.pixelSize: 11 }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    Btn { small: true; text: tab.renaming ? tab.tr.save : tab.tr.rename
                          onClicked: { if (tab.renaming) { settings.call("renameConversation", {id: tab.conv.id, title: titleField.text}); tab.renaming = false }
                                       else { tab.renaming = true; titleField.forceActiveFocus() } } }
                    Btn { small: true; text: "⇩ " + tab.tr.export_md; onClicked: settings.call("exportConversation", {id: tab.conv.id, fmt: "md"}) }
                    Btn { small: true; text: "⇩ " + tab.tr.export_json; onClicked: settings.call("exportConversation", {id: tab.conv.id, fmt: "json"}) }
                    DangerBtn { small: true; normalText: tab.tr.delete; confirmText: tab.tr.confirm_again
                                onConfirmed: settings.call("deleteConversation", {id: tab.conv.id}) }
                }
                ListView {
                    id: msgs
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: tab.conv.messages || []
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: Item {
                        required property var modelData
                        readonly property bool mine: modelData.role === "user"
                        width: msgs.width - 10
                        height: bubble.height + who.height + 4
                        Text { id: who; x: parent.mine ? parent.width - width : 0; text: modelData.who + " · " + modelData.when; color: tab.th.dim; font.pixelSize: 10 }
                        Rectangle {
                            id: bubble
                            y: who.height + 3
                            x: parent.mine ? parent.width - width : 0
                            width: Math.min(body.implicitWidth + 24, msgs.width * 0.8)
                            height: body.implicitHeight + 16
                            radius: 12
                            color: parent.mine ? tab.th.accent : tab.th.card2
                            Text { id: body; x: 12; y: 8; width: bubble.width - 24; text: modelData.text; wrapMode: Text.Wrap
                                   color: parent.parent.mine ? "white" : tab.th.fg; font.pixelSize: 13 }
                        }
                    }
                }
            }
        }
    }
}
