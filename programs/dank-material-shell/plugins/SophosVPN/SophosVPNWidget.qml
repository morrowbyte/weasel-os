import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    pillRightClickAction: () => root.toggleQuickAction()

    property string connectionName: "Sophos VPN"
    property string profilePath: ""
    property string username: "tobias.wehrle"
    property string basePassword: ""
    property string totp: ""
    property string state: "unknown"
    property string barText: "VPN ..."
    property string detailText: "Checking"
    property string messageText: ""
    property string deviceText: ""
    property string ip4Text: ""
    property bool connected: false
    property bool bridgeConnected: false
    property bool bridgeManaged: false
    property string bridgeLocal: "127.0.0.1:13389"
    property string bridgeTarget: "10.145.5.50:3389"
    property string streamHost: "10.145.5.50"
    property string streamControl: "10.145.5.50:47984"
    property string streamRouteDevice: "tailscale0"
    property bool streamRouteReady: false
    property bool streamTcpReachable: false
    property string streamStatus: "route-missing"
    property bool hasBasePassword: false
    property bool busy: false
    property string pendingAction: ""
    property string statusErrorText: ""
    property string actionErrorText: ""

    function helperEnv() {
        return {
            "SOPHOS_VPN_CONNECTION_NAME": connectionName
        }
    }

    function statusColor() {
        if (state === "connected" || state === "bridge-connected") return Theme.success
        if (state === "connecting") return Theme.warning
        if (state === "error" || state === "missing" || state === "conflict") return Theme.error
        return Theme.surfaceVariantText
    }

    function statusIcon() {
        if (connected) return "vpn_lock"
        if (bridgeConnected) return "desktop_windows"
        if (busy) return "sync"
        return "vpn_key"
    }

    function sanitizedMessage(value) {
        if (!value) return ""
        return value.replace(/\s+/g, " ").trim()
    }

    function parsePayload(raw) {
        const text = raw.trim()
        if (!text) return

        let data
        try {
            data = JSON.parse(text)
        } catch (error) {
            state = "error"
            barText = "VPN ERR"
            detailText = "Invalid helper output"
            messageText = error.toString()
            busy = false
            pendingAction = ""
            return
        }

        if (data.connection) connectionName = data.connection
        state = data.state || (data.connected ? "connected" : "disconnected")
        connected = Boolean(data.vpn_connected !== undefined ? data.vpn_connected : data.connected)
        bridgeConnected = Boolean(data.bridge_connected)
        bridgeManaged = Boolean(data.bridge_managed)
        bridgeLocal = data.bridge_local || bridgeLocal
        bridgeTarget = data.bridge_target || bridgeTarget
        streamHost = data.stream_host || streamHost
        streamControl = data.stream_control || streamControl
        streamRouteDevice = data.stream_route_device || streamRouteDevice
        streamRouteReady = Boolean(data.stream_route_ready)
        streamTcpReachable = Boolean(data.stream_tcp_reachable)
        streamStatus = data.stream_status || "route-missing"
        hasBasePassword = Boolean(data.has_base_password)
        barText = data.bar_text || (connected ? "VPN ON" : "VPN OFF")
        detailText = data.detail_text || data.message || ""
        deviceText = data.device || ""
        ip4Text = data.ip4 || ""
        messageText = sanitizedMessage(data.message || "")
        busy = false
        pendingAction = ""
        if (data.action === "connect") totp = ""
        if (data.action === "store-password") basePassword = ""
    }

    function handleProcessFailure(label, exitCode, stderrText) {
        busy = false
        pendingAction = ""
        state = "error"
        connected = false
        barText = "VPN ERR"
        detailText = label
        messageText = sanitizedMessage(stderrText) || label + " failed (exit " + exitCode + ")"
    }

    function refreshStatus() {
        if (busy || statusProcess.running) return
        statusProcess.environment = helperEnv()
        statusErrorText = ""
        statusProcess.running = true
    }

    function importProfile() {
        if (!profilePath.trim()) {
            messageText = "Set the .ovpn path first"
            return
        }
        if (!username.trim()) {
            messageText = "Set the VPN username first"
            return
        }

        busy = true
        pendingAction = "import"
        actionErrorText = ""
        actionProcess.stdinEnabled = false
        actionProcess.command = ["sophos-vpn", "import", profilePath.trim(), "--name", connectionName, "--username", username.trim()]
        actionProcess.environment = helperEnv()
        actionProcess.running = true
    }

    function storePassword() {
        if (!basePassword) {
            messageText = "Enter the base password first"
            return
        }

        busy = true
        pendingAction = "store-password"
        actionErrorText = ""
        actionProcess.stdinEnabled = true
        actionProcess.command = ["sophos-vpn", "store-password"]
        actionProcess.environment = helperEnv()
        actionProcess.running = true
    }

    function connectVpn() {
        if (!totp.match(/^[0-9]{6}$/)) {
            messageText = "TOTP must be 6 digits"
            return
        }

        busy = true
        pendingAction = "connect"
        actionErrorText = ""
        actionProcess.stdinEnabled = true
        actionProcess.command = ["sophos-vpn", "connect"]
        actionProcess.environment = helperEnv()
        actionProcess.running = true
    }

    function disconnectVpn() {
        busy = true
        pendingAction = "disconnect"
        actionErrorText = ""
        actionProcess.stdinEnabled = false
        actionProcess.command = ["sophos-vpn", "disconnect"]
        actionProcess.environment = helperEnv()
        actionProcess.running = true
    }

    function connectBridge() {
        busy = true
        pendingAction = "bridge-connect"
        actionErrorText = ""
        actionProcess.stdinEnabled = false
        actionProcess.command = ["sophos-vpn", "bridge-connect"]
        actionProcess.environment = helperEnv()
        actionProcess.running = true
    }

    function disconnectBridge() {
        busy = true
        pendingAction = "bridge-disconnect"
        actionErrorText = ""
        actionProcess.stdinEnabled = false
        actionProcess.command = ["sophos-vpn", "bridge-disconnect"]
        actionProcess.environment = helperEnv()
        actionProcess.running = true
    }

    function toggleVpn() {
        if (busy || actionProcess.running) return
        if (connected) disconnectVpn()
        else connectVpn()
    }

    function toggleBridge() {
        if (busy || actionProcess.running) return
        if (bridgeConnected) disconnectBridge()
        else connectBridge()
    }

    function toggleQuickAction() {
        if (busy || actionProcess.running) return
        if (connected) {
            disconnectVpn()
        } else if (bridgeConnected) {
            disconnectBridge()
        } else if (hasBasePassword) {
            connectVpn()
        } else {
            messageText = "Save the base password before connecting"
        }
    }

    Component.onCompleted: refreshStatus()

    Timer {
        interval: 15000
        repeat: true
        running: true
        onTriggered: root.refreshStatus()
    }

    Process {
        id: statusProcess
        command: ["sophos-vpn", "status"]
        running: false

        stdout: StdioCollector {
            onStreamFinished: root.parsePayload(this.text)
        }

        stderr: StdioCollector {
            onStreamFinished: root.statusErrorText = this.text
        }

        onExited: (exitCode) => {
            if (exitCode !== 0) {
                root.handleProcessFailure("Status failed", exitCode, root.statusErrorText)
            }
        }
    }

    Process {
        id: actionProcess
        command: ["sophos-vpn", "status"]
        running: false
        stdinEnabled: false

        stdout: StdioCollector {
            onStreamFinished: root.parsePayload(this.text)
        }

        stderr: StdioCollector {
            onStreamFinished: root.actionErrorText = this.text
        }

        onStarted: {
            if (root.pendingAction === "store-password" && root.basePassword) {
                actionProcess.write(root.basePassword + "\n")
            } else if (root.pendingAction === "connect" && root.totp) {
                actionProcess.write(root.totp + "\n")
            }
        }

        onExited: (exitCode) => {
            if (exitCode !== 0) {
                root.handleProcessFailure(root.pendingAction || "Action failed", exitCode, root.actionErrorText)
            } else {
                root.refreshStatus()
            }
        }
    }

    component LabeledField: Column {
        property alias text: field.text
        property alias placeholderText: field.placeholderText
        property alias echoMode: field.echoMode
        property alias inputMethodHints: field.inputMethodHints
        property string label: ""
        property var fieldValidator: null

        spacing: Theme.spacingXS

        StyledText {
            text: parent.label
            color: Theme.surfaceVariantText
            font.pixelSize: Theme.fontSizeSmall
        }

        TextField {
            id: field
            validator: parent.fieldValidator
            color: Theme.surfaceText
            selectedTextColor: Theme.surface
            selectionColor: Theme.primary
            font.pixelSize: Theme.fontSizeSmall
            placeholderTextColor: Theme.surfaceVariantText
            background: Rectangle {
                radius: Theme.cornerRadius
                color: Theme.surfaceContainerHighest
                border.width: 1
                border.color: field.activeFocus ? Theme.primary : Theme.outline
            }
        }
    }

    component ActionButton: Button {
        property color accentColor: Theme.primary

        enabled: !root.busy
        font.pixelSize: Theme.fontSizeSmall
        contentItem: Text {
            text: parent.text
            color: Theme.surface
            font: parent.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: Theme.cornerRadius
            color: parent.enabled ? parent.accentColor : Theme.surfaceVariant
            opacity: parent.enabled ? 1 : 0.6
        }
    }

    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingXS

            DankIcon {
                name: root.statusIcon()
                size: Theme.iconSize - 6
                color: root.statusColor()
                anchors.verticalCenter: parent.verticalCenter
            }

            StyledText {
                text: root.barText
                font.pixelSize: Theme.fontSizeSmall
                font.weight: Font.Medium
                color: root.statusColor()
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: Theme.spacingXS

            DankIcon {
                name: root.statusIcon()
                size: Theme.iconSize - 6
                color: root.statusColor()
                anchors.horizontalCenter: parent.horizontalCenter
            }

            StyledText {
                text: root.barText
                font.pixelSize: Theme.fontSizeSmall
                font.weight: Font.Medium
                color: root.statusColor()
                anchors.horizontalCenter: parent.horizontalCenter
                rotation: 90
            }
        }
    }

    popoutContent: Component {
        PopoutComponent {
            headerText: "Blain Remote Access"
            detailsText: root.connectionName + " • " + root.detailText
            showCloseButton: true

            Column {
                width: parent.width
                spacing: Theme.spacingM

                StyledRect {
                    width: parent.width
                    height: statusLayout.implicitHeight + Theme.spacingM * 2
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHigh

                    Column {
                        id: statusLayout
                        anchors.fill: parent
                        anchors.margins: Theme.spacingM
                        spacing: Theme.spacingS

                        Row {
                            width: parent.width
                            spacing: Theme.spacingM

                            DankIcon {
                                name: root.statusIcon()
                                size: Theme.iconSize
                                color: root.statusColor()
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Column {
                                width: parent.width - Theme.iconSize - Theme.spacingM
                                spacing: 2

                                StyledText {
                                    text: root.barText
                                    color: root.statusColor()
                                    font.pixelSize: Theme.fontSizeMedium
                                    font.weight: Font.Medium
                                }

                                StyledText {
                                    width: parent.width
                                    elide: Text.ElideRight
                                    text: root.detailText + (root.ip4Text ? " • " + root.ip4Text : "")
                                    color: Theme.surfaceVariantText
                                    font.pixelSize: Theme.fontSizeSmall
                                }
                            }
                        }

                        GridLayout {
                            columns: 2
                            columnSpacing: Theme.spacingL
                            rowSpacing: Theme.spacingXS
                            width: parent.width

                            StyledText { text: "Connection"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.connectionName; color: Theme.surfaceText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Username"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.username || "-"; color: Theme.surfaceText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Base password"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.hasBasePassword ? "Stored" : "Missing"; color: root.hasBasePassword ? Theme.success : Theme.warning; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Tunnel"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.deviceText || "-"; color: Theme.surfaceText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "RDP endpoint"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.bridgeLocal; color: Theme.surfaceText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "RDP SSH target"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.bridgeTarget; color: Theme.surfaceText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Moonlight host"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.streamHost + " (direct, not SSH)"; color: Theme.surfaceText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Direct route"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.streamRouteReady ? "Ready via " + root.streamRouteDevice : "Missing via " + root.streamRouteDevice; color: root.streamRouteReady ? Theme.success : Theme.warning; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Sunshine control"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: root.streamTcpReachable ? root.streamControl + " reachable" : root.streamControl + " unreachable"; color: root.streamTcpReachable ? Theme.success : Theme.warning; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Streaming UDP"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                            StyledText { text: "Verify with a Moonlight stream"; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall }
                        }
                    }
                }

                LabeledField {
                    id: profileField
                    width: parent.width
                    label: "OpenVPN profile path"
                    text: root.profilePath
                    placeholderText: "/home/you/Downloads/sophos.ovpn"
                    onTextChanged: root.profilePath = text
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingM

                    LabeledField {
                        Layout.fillWidth: true
                        label: "Connection name"
                        text: root.connectionName
                        placeholderText: "Sophos VPN"
                        onTextChanged: root.connectionName = text
                    }

                    LabeledField {
                        Layout.fillWidth: true
                        label: "VPN username"
                        text: root.username
                        placeholderText: "tobias.wehrle"
                        onTextChanged: root.username = text
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingM

                    LabeledField {
                        Layout.fillWidth: true
                        label: "Base password"
                        text: root.basePassword
                        placeholderText: root.hasBasePassword ? "Stored - overwrite to replace" : "Store base password"
                        echoMode: TextInput.Password
                        onTextChanged: root.basePassword = text
                    }

                    LabeledField {
                        Layout.preferredWidth: 140
                        label: "TOTP"
                        text: root.totp
                        placeholderText: "123456"
                        echoMode: TextInput.Password
                        inputMethodHints: Qt.ImhDigitsOnly
                        fieldValidator: RegularExpressionValidator { regularExpression: /[0-9]{0,6}/ }
                        onTextChanged: root.totp = text.replace(/[^0-9]/g, "").slice(0, 6)
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingS

                    ActionButton {
                        Layout.fillWidth: true
                        text: "Import"
                        accentColor: Theme.secondary
                        onClicked: root.importProfile()
                    }

                    ActionButton {
                        Layout.fillWidth: true
                        text: root.hasBasePassword ? "Replace PW" : "Store PW"
                        accentColor: Theme.primary
                        onClicked: root.storePassword()
                    }

                    ActionButton {
                        Layout.fillWidth: true
                        text: root.connected ? "OpenVPN: ON" : "OpenVPN: OFF"
                        accentColor: root.connected ? Theme.error : Theme.success
                        onClicked: root.toggleVpn()
                    }

                    ActionButton {
                        Layout.fillWidth: true
                        text: root.bridgeConnected ? "RDP bridge: ON" : "RDP bridge: OFF"
                        accentColor: root.bridgeConnected ? Theme.error : Theme.secondary
                        onClicked: root.toggleBridge()
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingS

                    ActionButton {
                        Layout.fillWidth: true
                        text: root.busy ? "Working..." : "Refresh"
                        accentColor: Theme.surfaceVariantText
                        enabled: !root.busy
                        onClicked: root.refreshStatus()
                    }
                }

                StyledRect {
                    width: parent.width
                    height: messageLabel.implicitHeight + Theme.spacingM * 2
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHighest

                    StyledText {
                        id: messageLabel
                        anchors.fill: parent
                        anchors.margins: Theme.spacingM
                        wrapMode: Text.Wrap
                        text: root.messageText || "The SSH bridge carries RDP only (127.0.0.1:13389). Moonlight/Sunshine connects directly to 10.145.5.50; this check does not verify streaming UDP."
                        color: root.state === "error" || root.state === "missing" || root.state === "conflict" ? Theme.error : Theme.surfaceVariantText
                        font.pixelSize: Theme.fontSizeSmall
                    }
                }
            }
        }
    }
}
