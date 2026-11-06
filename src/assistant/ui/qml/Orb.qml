import QtQuick

// Animated orb: breathes with the mic level while listening, spins while thinking,
// follows the voice while speaking, settles when done. Plain Qt Quick items only, so it
// renders the same with the software backend (no GPU needed).
Item {
    id: orb
    property color accent: "#7C5CFF"
    property string mode: "idle"     // listening | thinking | acting | speaking | confirm | done | error | idle
    property real level: 0.0         // audio RMS 0..~0.3 (mic while listening, speech output while speaking)
    property real smooth: 0.0
    readonly property real voice: Math.min(1.0, smooth * 9.0)
    readonly property color tint: mode === "confirm" ? "#F5A524" : mode === "error" ? "#E5484D" : accent
    readonly property bool busy: mode === "thinking" || mode === "acting"

    Behavior on smooth { NumberAnimation { duration: 90 } }
    onLevelChanged: smooth = level

    // expanding rings while listening (louder voice -> stronger rings)
    Repeater {
        model: 2
        Rectangle {
            required property int index
            readonly property real p: (ticker.t + index * 0.5) % 1.0
            anchors.centerIn: parent
            width: orb.width * (0.66 + 0.34 * p); height: width
            radius: width / 2
            color: "transparent"
            border.width: Math.max(1.5, orb.width / 28)
            border.color: orb.tint
            opacity: orb.mode === "listening" ? (1.0 - p) * (0.2 + 0.7 * orb.voice) : 0.0
            visible: opacity > 0.01
        }
    }

    Rectangle {   // the orb itself
        id: core
        anchors.centerIn: parent
        readonly property real pulse: 0.5 + 0.5 * Math.sin(ticker.t * 2 * Math.PI * 2)
        width: orb.width * (orb.mode === "listening" ? 0.6 + 0.16 * orb.voice
                            : orb.mode === "speaking" ? 0.62 + 0.14 * orb.voice
                            : orb.mode === "confirm" ? 0.6 + 0.05 * pulse
                            : 0.62)
        height: width
        radius: width / 2
        Behavior on width { NumberAnimation { duration: 70 } }
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.lighter(orb.tint, 1.6) }
            GradientStop { position: 0.55; color: orb.tint }
            GradientStop { position: 1.0; color: Qt.darker(orb.tint, 1.8) }
        }
        rotation: orb.busy ? ticker.t * 360 : 0
        Rectangle {   // highlight
            width: parent.width * 0.34; height: width * 0.58; radius: height / 2
            x: parent.width * 0.2; y: parent.height * 0.13
            color: Qt.rgba(1, 1, 1, 0.33)
        }
    }

    Canvas {   // thinking / acting: an arc circling the orb
        id: arc
        anchors.fill: parent
        visible: orb.busy
        rotation: ticker.t * 720
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.lineWidth = Math.max(2, orb.width / 22)
            ctx.strokeStyle = orb.tint
            ctx.lineCap = "round"
            ctx.beginPath()
            ctx.arc(width / 2, height / 2, width / 2 - ctx.lineWidth, 0, Math.PI * 0.6)
            ctx.stroke()
        }
        onVisibleChanged: if (visible) requestPaint()
        Connections { target: orb; function onTintChanged() { arc.requestPaint() } }
    }

    Text {   // done / error / confirm marks
        anchors.centerIn: parent
        visible: orb.mode === "done" || orb.mode === "error" || orb.mode === "confirm"
        text: orb.mode === "done" ? "✓" : orb.mode === "confirm" ? "?" : "!"
        color: "white"
        font { pixelSize: Math.round(orb.width * 0.3); bold: true }
    }

    QtObject {
        id: ticker
        property real t: 0.0
    }
    NumberAnimation {
        target: ticker; property: "t"; from: 0.0; to: 1.0; duration: 1600
        loops: Animation.Infinite
        running: orb.visible && orb.mode !== "done" && orb.mode !== "idle" && orb.mode !== "error"
    }
}
