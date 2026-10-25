// Board Assistant window. Local Start / Pause / Resume / Stop only.
// Reads state from the `session` bridge (ui/gui.py). Loads nothing from the
// network. Shows only the latest question and answer (text after the trigger
// phrase), never other meeting text, and never audio.

import QtQuick
import QtQuick.Window
import QtQuick.Effects
import QtQuick.Shapes

Window {
    id: root
    width: 860
    height: 680
    minimumWidth: 560
    minimumHeight: 620
    visible: true
    title: "Board Assistant"
    color: theme.bg

    QtObject {
        id: theme
        readonly property color bg: "#0B0D10"
        readonly property color surface: "#14181D"
        readonly property color line: "#232931"
        readonly property color text: "#ECEEF1"
        readonly property color muted: "#8D96A1"
        readonly property color faint: "#59616C"
        readonly property color accent: "#4FD1C5"
        readonly property color accentBright: "#86F0E4"
        readonly property color steel: "#6E7884"
        readonly property color amber: "#E4B866"
        readonly property color danger: "#E8776E"
        readonly property string family: "Segoe UI"
    }

    readonly property string status: session.status
    readonly property bool speaking: session.speaking
    readonly property bool listening: status === "listening"
    readonly property bool answering: (listening || status === "finishing") && speaking
    readonly property string dialogue: session.dialogue
    readonly property bool heard: listening && dialogue === "heard"
    readonly property bool thinking: listening && dialogue === "asked" && !speaking
    readonly property bool busy: status === "connecting" || status === "stopping"
                                 || (status === "finishing" && !speaking) || thinking
    readonly property bool active: status !== "idle"
    readonly property bool started: status === "listening" || status === "paused" || status === "interrupted"
    readonly property bool cardShown: active && (session.question !== "" || session.answer !== ""
                                                 || dialogue === "heard" || dialogue === "unavailable")

    readonly property color tone: {
        if (status === "paused") return theme.amber
        if (status === "interrupted") return theme.danger
        if (status === "idle") return theme.steel
        return (answering || heard) ? theme.accentBright : theme.accent
    }

    readonly property string headline: {
        if (session.closing && active) return "Closing"
        switch (status) {
        case "connecting": return "Connecting"
        case "listening":
            if (speaking) return "Answering"
            if (heard) return "I'm listening"
            if (thinking) return "Thinking"
            return "Listening"
        case "paused": return "Paused"
        case "stopping": return "Ending session"
        case "finishing": return "Finishing the answer"
        case "interrupted": return "Audio interrupted"
        default: return "Ready when you are"
        }
    }

    readonly property string subline: {
        if (session.closing && active) return "The window will close once the session has ended."
        switch (status) {
        case "connecting": return "Opening a secure link to the assistant."
        case "listening":
            if (speaking) return "Capture is muted while the answer plays."
            if (heard) return "Go ahead and ask your question."
            if (thinking) return "Finding an answer to your question."
            return "The meeting is being transcribed. Say the trigger phrase to ask a question."
        case "paused": return "Nothing is captured or transcribed until you resume."
        case "stopping": return "Closing capture and the secure link."
        case "finishing": return "The session has ended. The answer will finish playing."
        case "interrupted": return "Audio is no longer reaching the assistant. Stop, then start a new session."
        default: return "Start a session to transcribe the meeting. Say the trigger phrase to ask a question."
        }
    }

    onClosing: (close) => { close.accepted = session.requestClose() }

    // ---------------------------------------------------------------- ambient
    Item {
        id: ambient
        anchors.fill: parent

        Rectangle {
            id: ambientSource
            width: Math.min(root.width, root.height) * 0.9
            height: width
            radius: width / 2
            anchors.horizontalCenter: parent.horizontalCenter
            y: stage.y + content.y + orb.height / 2 - height / 2
            color: root.tone
            visible: false
            layer.enabled: true
            Behavior on color { ColorAnimation { duration: 700; easing.type: Easing.InOutQuad } }
        }
        MultiEffect {
            source: ambientSource
            anchors.fill: ambientSource
            blurEnabled: true
            blur: 1.0
            blurMax: 64
            opacity: root.status === "idle" ? 0.04 : (root.answering ? 0.13 : 0.08)
            Behavior on opacity { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
        }
    }

    // ----------------------------------------------------------------- header
    Item {
        id: header
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 28 }
        height: 36

        Row {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 12
            Rectangle {
                width: 10; height: 10; radius: 5
                anchors.verticalCenter: parent.verticalCenter
                color: root.tone
                Behavior on color { ColorAnimation { duration: 500 } }
            }
            Text {
                text: "BOARD ASSISTANT"
                color: theme.muted
                font { family: theme.family; pixelSize: 12; weight: Font.DemiBold; letterSpacing: 3 }
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        // Status pill
        Rectangle {
            id: pill
            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            height: 32
            width: pillRow.implicitWidth + 28
            radius: height / 2
            color: Qt.rgba(root.tone.r, root.tone.g, root.tone.b, 0.08)
            border.color: Qt.rgba(root.tone.r, root.tone.g, root.tone.b, 0.35)
            border.width: 1
            Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

            Row {
                id: pillRow
                anchors.centerIn: parent
                spacing: 9
                Item {
                    width: 8; height: 8
                    anchors.verticalCenter: parent.verticalCenter
                    Rectangle {
                        anchors.centerIn: parent
                        width: 8; height: 8; radius: 4
                        color: root.tone
                    }
                    Rectangle {
                        id: pillPulse
                        anchors.centerIn: parent
                        width: 8; height: 8; radius: 4
                        color: root.tone
                        opacity: 0
                        SequentialAnimation on scale {
                            running: root.listening
                            loops: Animation.Infinite
                            NumberAnimation { from: 1; to: 2.6; duration: 1400; easing.type: Easing.OutCubic }
                            PauseAnimation { duration: 300 }
                        }
                        SequentialAnimation on opacity {
                            running: root.listening
                            loops: Animation.Infinite
                            NumberAnimation { from: 0.6; to: 0; duration: 1400; easing.type: Easing.OutCubic }
                            PauseAnimation { duration: 300 }
                        }
                    }
                }
                Text {
                    text: {
                        switch (root.status) {
                        case "listening":
                            if (root.speaking) return "Answering"
                            if (root.heard) return "Question"
                            if (root.thinking) return "Thinking"
                            return "Live"
                        case "paused": return "Paused"
                        case "interrupted": return "Interrupted"
                        case "idle": return "Not recording"
                        default: return "Working"
                        }
                    }
                    color: theme.text
                    font { family: theme.family; pixelSize: 13; weight: Font.Medium }
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }

    // ------------------------------------------------------------------ stage
    // Orb, status, timer and buttons in one centred column, so nothing
    // overlaps however the text wraps.
    Item {
        id: stage
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: footer.top }

    Column {
        id: content
        anchors.centerIn: parent
        spacing: 0

    // -------------------------------------------------------------------- orb
    Item {
        id: orb
        width: root.cardShown ? (root.height < 760 ? 160 : 200) : (root.height < 720 ? 250 : 300)
        height: width
        anchors.horizontalCenter: parent.horizontalCenter
        Behavior on width { NumberAnimation { duration: 520; easing.type: Easing.InOutCubic } }

        readonly property real core: Math.round(width * 0.587)

        // Trigger heard: one bright burst from the core, then the orb stays lit.
        Connections {
            target: root
            function onHeardChanged() { if (root.heard) burst.restart() }
        }
        Rectangle {
            id: burstRing
            anchors.centerIn: parent
            width: orb.core; height: width; radius: width / 2
            color: Qt.rgba(theme.accentBright.r, theme.accentBright.g, theme.accentBright.b, 0.18)
            border.width: 3
            border.color: theme.accentBright
            opacity: 0
            z: 2
        }
        Rectangle {
            id: burstEcho
            anchors.centerIn: parent
            width: orb.core; height: width; radius: width / 2
            color: "transparent"
            border.width: 2
            border.color: theme.accentBright
            opacity: 0
            z: 2
        }
        ParallelAnimation {
            id: burst
            NumberAnimation { target: burstRing; property: "scale"; from: 1.0; to: 1.9; duration: 700; easing.type: Easing.OutCubic }
            NumberAnimation { target: burstRing; property: "opacity"; from: 0.95; to: 0; duration: 700; easing.type: Easing.OutCubic }
            SequentialAnimation {
                PauseAnimation { duration: 160 }
                ParallelAnimation {
                    NumberAnimation { target: burstEcho; property: "scale"; from: 1.0; to: 2.3; duration: 900; easing.type: Easing.OutCubic }
                    NumberAnimation { target: burstEcho; property: "opacity"; from: 0.7; to: 0; duration: 900; easing.type: Easing.OutCubic }
                }
            }
        }

        // Listening ripples: slow and thin.
        Repeater {
            model: 3
            Rectangle {
                id: listenRing
                required property int index
                anchors.centerIn: parent
                width: orb.core; height: orb.core; radius: width / 2
                color: "transparent"
                border.width: 1.5
                border.color: root.tone
                opacity: 0
                visible: root.listening && !root.speaking
                SequentialAnimation {
                    running: root.listening && !root.speaking
                    loops: Animation.Infinite
                    PauseAnimation { duration: listenRing.index * 1100 }
                    ParallelAnimation {
                        NumberAnimation { target: listenRing; property: "scale"; from: 1; to: 1.75; duration: 3300; easing.type: Easing.OutSine }
                        NumberAnimation { target: listenRing; property: "opacity"; from: 0.45; to: 0; duration: 3300; easing.type: Easing.OutSine }
                    }
                    onStopped: { listenRing.opacity = 0; listenRing.scale = 1 }
                }
            }
        }

        // Answer ripples: faster, brighter, filled.
        Repeater {
            model: 4
            Rectangle {
                id: answerRing
                required property int index
                anchors.centerIn: parent
                width: orb.core; height: orb.core; radius: width / 2
                color: Qt.rgba(root.tone.r, root.tone.g, root.tone.b, 0.10)
                border.width: 2
                border.color: root.tone
                opacity: 0
                visible: root.answering
                SequentialAnimation {
                    running: root.answering
                    loops: Animation.Infinite
                    PauseAnimation { duration: answerRing.index * 380 }
                    ParallelAnimation {
                        NumberAnimation { target: answerRing; property: "scale"; from: 1; to: 1.7; duration: 1520; easing.type: Easing.OutQuad }
                        NumberAnimation { target: answerRing; property: "opacity"; from: 0.7; to: 0; duration: 1520; easing.type: Easing.OutQuad }
                    }
                    onStopped: { answerRing.opacity = 0; answerRing.scale = 1 }
                }
            }
        }

        // Soft light behind the core.
        Rectangle {
            id: glowSource
            anchors.centerIn: parent
            width: orb.core * 1.05; height: width; radius: width / 2
            color: root.tone
            visible: false
            layer.enabled: true
            Behavior on color { ColorAnimation { duration: 600 } }
        }
        MultiEffect {
            id: glow
            source: glowSource
            anchors.fill: glowSource
            blurEnabled: true
            blur: 1.0
            blurMax: 48
            opacity: root.status === "idle" ? 0.18 : (root.status === "paused" ? 0.28 : 0.5)
            Behavior on opacity { NumberAnimation { duration: 700 } }
            SequentialAnimation on scale {
                loops: Animation.Infinite
                running: true
                NumberAnimation { from: 1.0; to: 1.08; duration: root.answering ? 700 : 2600; easing.type: Easing.InOutSine }
                NumberAnimation { from: 1.08; to: 1.0; duration: root.answering ? 700 : 2600; easing.type: Easing.InOutSine }
            }
        }

        // Glass core.
        Rectangle {
            id: core
            anchors.centerIn: parent
            width: orb.core; height: width; radius: width / 2
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#1A2027" }
                GradientStop { position: 1.0; color: "#0C0F13" }
            }
            border.width: 1.5
            border.color: Qt.rgba(root.tone.r, root.tone.g, root.tone.b, root.status === "idle" ? 0.35 : 0.75)
            Behavior on border.color { ColorAnimation { duration: 600 } }

            SequentialAnimation on scale {
                loops: Animation.Infinite
                running: root.status !== "paused"
                NumberAnimation { from: 1.0; to: 1.025; duration: 2600; easing.type: Easing.InOutSine }
                NumberAnimation { from: 1.025; to: 1.0; duration: 2600; easing.type: Easing.InOutSine }
            }

            // Top sheen: a soft elliptical highlight on the glass.
            Shape {
                id: sheen
                anchors.horizontalCenter: parent.horizontalCenter
                y: parent.height * 0.06
                width: parent.width * 0.7; height: parent.height * 0.36
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: "transparent"
                    fillGradient: LinearGradient {
                        x1: 0; y1: 0; x2: 0; y2: sheen.height
                        GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.075) }
                        GradientStop { position: 0.7; color: Qt.rgba(1, 1, 1, 0.0) }
                    }
                    PathAngleArc {
                        centerX: sheen.width / 2; centerY: sheen.height / 2
                        radiusX: sheen.width / 2; radiusY: sheen.height / 2
                        startAngle: 0; sweepAngle: 360
                    }
                }
            }

            // Wave: decorative, not an audio level meter.
            Row {
                id: wave
                anchors.centerIn: parent
                spacing: 7
                opacity: (root.listening || root.answering) ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 400 } }
                Repeater {
                    model: 5
                    Rectangle {
                        id: bar
                        required property int index
                        readonly property real rest: [14, 24, 32, 24, 14][index]
                        width: 6; radius: 3
                        height: rest
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.tone
                        SequentialAnimation on height {
                            loops: Animation.Infinite
                            running: wave.visible
                            PauseAnimation { duration: bar.index * 120 }
                            NumberAnimation { to: bar.rest * (root.answering ? 2.0 : 1.35); duration: root.answering ? 260 : 900; easing.type: Easing.InOutSine }
                            NumberAnimation { to: bar.rest * (root.answering ? 0.55 : 0.8); duration: root.answering ? 260 : 900; easing.type: Easing.InOutSine }
                        }
                    }
                }
            }

            // Pause glyph.
            Row {
                anchors.centerIn: parent
                spacing: 14
                opacity: root.status === "paused" ? 1 : 0
                scale: root.status === "paused" ? 1 : 0.7
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 350 } }
                Behavior on scale { NumberAnimation { duration: 450; easing.type: Easing.OutBack } }
                Repeater {
                    model: 2
                    Rectangle { width: 10; height: 46; radius: 4; color: theme.amber }
                }
            }

            // Idle glyph.
            Rectangle {
                anchors.centerIn: parent
                width: 18; height: 18; radius: 9
                color: "transparent"
                border.width: 2
                border.color: theme.steel
                opacity: root.status === "idle" ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 350 } }
            }

            // Interrupted glyph.
            Column {
                anchors.centerIn: parent
                spacing: 6
                opacity: root.status === "interrupted" ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 350 } }
                Rectangle { width: 6; height: 30; radius: 3; color: theme.danger; anchors.horizontalCenter: parent.horizontalCenter }
                Rectangle { width: 6; height: 6; radius: 3; color: theme.danger; anchors.horizontalCenter: parent.horizontalCenter }
            }
        }

        // Spinner for connecting / ending.
        Shape {
            id: spinner
            anchors.centerIn: parent
            width: orb.core + 22; height: width
            opacity: root.busy ? 1 : 0
            visible: opacity > 0
            preferredRendererType: Shape.CurveRenderer
            Behavior on opacity { NumberAnimation { duration: 300 } }
            ShapePath {
                strokeColor: root.tone
                strokeWidth: 2.5
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                PathAngleArc {
                    centerX: spinner.width / 2; centerY: spinner.height / 2
                    radiusX: spinner.width / 2 - 2; radiusY: radiusX
                    startAngle: 0; sweepAngle: 80
                }
            }
            RotationAnimation on rotation {
                running: spinner.visible
                loops: Animation.Infinite
                from: 0; to: 360; duration: 1100
            }
        }
    }

    // ------------------------------------------------------------ status text
    Column {
        id: statusText
        topPadding: 14
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(root.width - 80, 560)
        spacing: 10

        FadeText {
            width: parent.width
            value: root.headline
            color: theme.text
            font { family: theme.family; pixelSize: 30; weight: Font.Light }
        }
        // Collapses while the question card is up: the card says the same thing.
        Item {
            width: parent.width
            height: root.cardShown ? 0 : 40
            opacity: root.cardShown ? 0 : 1
            clip: true
            Behavior on height { NumberAnimation { duration: 420; easing.type: Easing.InOutCubic } }
            Behavior on opacity { NumberAnimation { duration: 250 } }
            FadeText {
                width: parent.width
                value: root.subline
                color: theme.muted
                height: 40
                verticalAlignment: Text.AlignTop
                wrapMode: Text.WordWrap
                lineHeight: 1.25
                font { family: theme.family; pixelSize: 14 }
            }
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 10
            opacity: root.status !== "connecting" && (root.active || session.elapsed !== "00:00:00") ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 400 } }
            Text {
                text: session.elapsed
                color: theme.text
                font { family: theme.family; pixelSize: 17; weight: Font.Medium }
                font.features: ({ "tnum": 1 })
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                id: timerLabel
                anchors.verticalCenter: parent.verticalCenter
                text: "LISTENED"
                color: theme.faint
                font { family: theme.family; pixelSize: 11; weight: Font.DemiBold; letterSpacing: 2 }
            }
        }
    }

    // ---------------------------------------------------- question and answer
    // Shown only after the trigger phrase. Held until the next question and
    // cleared on Stop. Nothing else said in the meeting reaches the window.
    Item {
        id: dialogueCard
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(root.width - 64, 640)
        // Never taller than the space left between the status text and the buttons;
        // longer text scrolls inside the card, pinned to the newest words.
        readonly property real room: stage.height - orb.height - statusText.height - controlsRow.height - 24
        height: root.cardShown ? Math.min(cardColumn.implicitHeight + 36, Math.max(96, room - 18)) + 18 : 0
        opacity: root.cardShown ? 1 : 0
        clip: true
        Behavior on height { NumberAnimation { duration: 420; easing.type: Easing.InOutCubic } }
        Behavior on opacity { NumberAnimation { duration: 320 } }

        Rectangle {
            id: cardFace
            anchors { left: parent.left; right: parent.right; top: parent.top; topMargin: 18; bottom: parent.bottom }
            radius: 16
            color: theme.surface
            border.width: 1
            border.color: root.heard ? Qt.rgba(theme.accentBright.r, theme.accentBright.g, theme.accentBright.b, 0.55)
                                     : theme.line
            Behavior on border.color { ColorAnimation { duration: 400 } }

            Flickable {
                id: cardScroll
                anchors { fill: parent; margins: 18 }
                contentHeight: cardColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                // Show from the top (question first); follow the newest words only on overflow.
                onContentHeightChanged: contentY = Math.max(0, contentHeight - height)
                onHeightChanged: contentY = Math.max(0, contentHeight - height)

                Column {
                    id: cardColumn
                    width: cardScroll.width
                    spacing: 8

                    Text {
                        text: "QUESTION"
                        color: root.heard ? theme.accentBright : theme.faint
                        font { family: theme.family; pixelSize: 11; weight: Font.DemiBold; letterSpacing: 2 }
                        Behavior on color { ColorAnimation { duration: 300 } }
                    }
                    Text {
                        id: questionText
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: session.question !== "" ? session.question + (root.heard ? "  ▍" : "")
                                                      : (root.heard ? "Listening for your question…" : "")
                        color: session.question !== "" ? theme.text : theme.muted
                        font { family: theme.family; pixelSize: 17; italic: root.heard; weight: Font.Normal }
                        lineHeight: 1.2
                    }

                    Item { width: 1; height: 6; visible: answerBlock.visible }

                    Column {
                        id: answerBlock
                        width: parent.width
                        spacing: 8
                        visible: root.dialogue === "asked" || root.dialogue === "answered" || root.dialogue === "unavailable"

                        Text {
                            text: "ANSWER"
                            color: theme.faint
                            font { family: theme.family; pixelSize: 11; weight: Font.DemiBold; letterSpacing: 2 }
                        }
                        Text {
                            id: answerText
                            width: parent.width
                            wrapMode: Text.WordWrap
                            property int shown: 0
                            text: {
                                if (root.dialogue === "unavailable") return "The assistant couldn't answer that question."
                                if (session.answer === "") return "Thinking…"
                                return session.answer.substring(0, shown)
                            }
                            color: session.answer === "" ? theme.muted : theme.text
                            font { family: theme.family; pixelSize: 15 }
                            lineHeight: 1.3
                            // Reveal the answer at roughly speaking pace.
                            NumberAnimation on shown {
                                id: reveal
                                running: false
                            }
                            Connections {
                                target: session
                                function onChanged() {
                                    if (session.answer !== answerText.lastAnswer) {
                                        answerText.lastAnswer = session.answer
                                        reveal.stop()
                                        answerText.shown = 0
                                        reveal.from = 0
                                        reveal.to = session.answer.length
                                        reveal.duration = Math.min(9000, session.answer.length * 38)
                                        if (session.answer !== "") reveal.start()
                                    }
                                }
                            }
                            property string lastAnswer: ""
                        }
                    }
                }
            }
        }
    }

    // --------------------------------------------------------------- controls
    Row {
        id: controlsRow
        anchors.horizontalCenter: parent.horizontalCenter
        topPadding: 28
        spacing: 14

        PillButton {
            id: pauseButton
            label: session.canResume ? "Resume" : "Pause"
            glyph: session.canResume ? "resume" : "pause"
            tint: session.canResume ? theme.accent : theme.amber
            enabled: session.canPause || session.canResume
            shown: root.started
            onClicked: session.canResume ? session.resume() : session.pause()
        }
        PillButton {
            id: mainButton
            primary: !root.active
            label: root.active ? "Stop" : "Start session"
            glyph: root.active ? "stop" : "start"
            tint: root.active ? theme.danger : theme.accent
            enabled: session.canStart || session.canStop
            onClicked: root.active ? session.stop() : session.start()
        }
    }
    }
    }

    Text {
        id: footer
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 22
        width: root.width - 56
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: "Pause keeps a discussion out of the transcript  ·  Controls work only on this computer"
        color: theme.faint
        font { family: theme.family; pixelSize: 12 }
    }

    // ----------------------------------------------------------- error banner
    Rectangle {
        id: banner
        readonly property bool shown: session.error !== ""
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(root.width - 56, 620)
        height: bannerText.implicitHeight + 28
        y: shown ? header.y + header.height + 14 : -height - 20
        radius: 12
        color: "#2A1716"
        border.color: Qt.rgba(theme.danger.r, theme.danger.g, theme.danger.b, 0.5)
        border.width: 1
        opacity: shown ? 1 : 0
        Behavior on y { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: 300 } }

        Text {
            id: bannerText
            anchors { left: parent.left; right: dismiss.left; verticalCenter: parent.verticalCenter; leftMargin: 18; rightMargin: 12 }
            text: session.error
            wrapMode: Text.WordWrap
            color: "#F4D3D0"
            font { family: theme.family; pixelSize: 13 }
        }
        Text {
            id: dismiss
            anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: 16 }
            text: "✕"
            color: dismissArea.containsMouse ? theme.text : theme.muted
            font { family: theme.family; pixelSize: 14 }
            MouseArea {
                id: dismissArea
                anchors.fill: parent
                anchors.margins: -8
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: session.dismissError()
            }
        }
    }

    // ------------------------------------------------------------- components
    component FadeText: Text {
        id: fade
        property string value
        horizontalAlignment: Text.AlignHCenter
        Component.onCompleted: text = value
        onValueChanged: swap.restart()
        SequentialAnimation {
            id: swap
            NumberAnimation { target: fade; property: "opacity"; to: 0; duration: 140 }
            ScriptAction { script: fade.text = fade.value }
            NumberAnimation { target: fade; property: "opacity"; to: 1; duration: 260; easing.type: Easing.OutQuad }
        }
    }

    component PillButton: Item {
        id: button
        property string label
        property string glyph
        property color tint: theme.accent
        property bool primary: false
        property bool shown: true
        signal clicked()

        implicitHeight: 52
        width: shown ? content.implicitWidth + 52 : 0
        height: implicitHeight
        opacity: shown ? (enabled ? 1 : 0.4) : 0
        visible: width > 1
        clip: true
        Behavior on width { NumberAnimation { duration: 380; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: 250 } }

        Rectangle {
            id: face
            anchors.fill: parent
            radius: height / 2
            color: button.primary
                   ? (area.containsMouse ? Qt.lighter(button.tint, 1.12) : button.tint)
                   : Qt.rgba(button.tint.r, button.tint.g, button.tint.b, area.containsMouse ? 0.16 : 0.08)
            border.width: button.primary ? 0 : 1
            border.color: Qt.rgba(button.tint.r, button.tint.g, button.tint.b, 0.55)
            scale: area.pressed ? 0.96 : 1
            Behavior on color { ColorAnimation { duration: 200 } }
            Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutQuad } }
        }

        Row {
            id: content
            anchors.centerIn: parent
            spacing: 10

            Item {
                id: icon
                width: 14; height: 14
                anchors.verticalCenter: parent.verticalCenter
                readonly property color ink: button.primary ? "#06211E" : button.tint
                // start / resume: triangle
                Shape {
                    anchors.fill: parent
                    visible: button.glyph === "start" || button.glyph === "resume"
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        fillColor: icon.ink
                        strokeColor: "transparent"
                        startX: 2; startY: 1
                        PathLine { x: 13; y: 7 }
                        PathLine { x: 2; y: 13 }
                        PathLine { x: 2; y: 1 }
                    }
                }
                // pause: two bars
                Row {
                    anchors.centerIn: parent
                    spacing: 4
                    visible: button.glyph === "pause"
                    Rectangle { width: 4; height: 13; radius: 1.5; color: icon.ink }
                    Rectangle { width: 4; height: 13; radius: 1.5; color: icon.ink }
                }
                // stop: rounded square
                Rectangle {
                    anchors.centerIn: parent
                    visible: button.glyph === "stop"
                    width: 12; height: 12; radius: 2.5
                    color: icon.ink
                }
            }
            Text {
                text: button.label
                anchors.verticalCenter: parent.verticalCenter
                color: button.primary ? "#06211E" : theme.text
                font { family: theme.family; pixelSize: 15; weight: Font.DemiBold }
            }
        }

        MouseArea {
            id: area
            anchors.fill: parent
            hoverEnabled: true
            enabled: button.enabled && button.shown
            cursorShape: Qt.PointingHandCursor
            onClicked: button.clicked()
        }
    }
}
