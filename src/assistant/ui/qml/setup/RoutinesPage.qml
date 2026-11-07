import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: page
    property var q: ({})
    property var items: (q.value || []).map(function(x) { return Object.assign({}, x) })
    readonly property var th: setup.theme
    function currentValue() { return items }
    function put(i, k, v) { var a = items.slice(); a[i] = Object.assign({}, a[i]); a[i][k] = v; items = a }
    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ColumnLayout {
            width: Math.min(parent.width, 640)
            spacing: 10
            Repeater {
                model: page.items
                Rectangle {
                    id: card
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.preferredHeight: modelData.enabled ? body.implicitHeight + 64 : 52
                    radius: 11
                    color: page.th.card
                    border.width: modelData.enabled ? 2 : 1
                    border.color: modelData.enabled ? page.th.accent : page.th.border
                    clip: true
                    OptionRow {
                        x: 0; y: 0; width: parent.width
                        multi: true
                        label: card.modelData.name
                        selected: card.modelData.enabled === true
                        color: "transparent"
                        border.width: 0
                        onPicked: page.put(card.index, "enabled", !card.modelData.enabled)
                    }
                    GridLayout {
                        id: body
                        visible: card.modelData.enabled === true
                        anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 52; leftMargin: 16; rightMargin: 16 }
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 8
                        Text { text: setup.ui.name || "Name"; color: page.th.dim; font.pixelSize: 12 }
                        TextField {
                            Layout.fillWidth: true
                            text: card.modelData.name
                            color: page.th.fg; font.pixelSize: 13; padding: 7
                            background: Rectangle { radius: 8; color: page.th.field; border.width: 1; border.color: page.th.border }
                            onTextEdited: page.put(card.index, "name", text)
                        }
                        Text { text: setup.ui.apps || "Apps"; color: page.th.dim; font.pixelSize: 12; visible: card.modelData.apps !== "" || card.modelData.key === "gaming" || card.modelData.key === "work" }
                        TextField {
                            visible: card.modelData.apps !== "" || card.modelData.key === "gaming" || card.modelData.key === "work"
                            Layout.fillWidth: true
                            text: card.modelData.apps
                            color: page.th.fg; font.pixelSize: 13; padding: 7
                            background: Rectangle { radius: 8; color: page.th.field; border.width: 1; border.color: page.th.border }
                            onTextEdited: page.put(card.index, "apps", text)
                        }
                        Text { visible: card.modelData.volume !== null && card.modelData.volume !== undefined
                               text: (setup.ui.volume || "Volume") + " " + Math.round(vol.value) + "%"; color: page.th.dim; font.pixelSize: 12 }
                        Slider {
                            id: vol
                            visible: card.modelData.volume !== null && card.modelData.volume !== undefined
                            Layout.fillWidth: true
                            from: 0; to: 100; stepSize: 5
                            value: card.modelData.volume || 0
                            onMoved: page.put(card.index, "volume", Math.round(value))
                        }
                    }
                }
            }
        }
    }
}
