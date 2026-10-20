import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    popoutWidth: 320
    pillRightClickAction: () => {
        if (root.connected && !root.busy) root.runAction("disconnect")
        else root.triggerPopout()
    }

    // Keep the installed plugin identity and original connection during migration.
    property string connectionName: "Sophos VPN"
    property string username: ""
    property string profilePath: ""
    property bool useTotp: true
    property string basePassword: ""
    property string totp: ""
    property string state: "checking"
    property bool connected: false
    property bool hasBasePassword: false
    property bool busy: false
    property string pendingAction: ""
    property string messageText: ""
    property string deviceText: ""
    property string ip4Text: ""
    property int selectedTab: 0
    property bool showDetails: false
    property bool replacePassword: false
    property bool profileChanged: false
    property bool saving: false
    property bool loadingSettings: false
    property bool receivedStatus: false

    readonly property string statusText: {
        if (busy && pendingAction === "connect") return "Verbinden …"
        if (busy && pendingAction === "disconnect") return "Trennen …"
        if (connected) return "Verbunden"
        if (state === "checking") return "Wird geprüft …"
        if (state === "missing") return "Profil fehlt"
        if (state === "error") return "Verbindungsfehler"
        return "Nicht verbunden"
    }
    readonly property color statusColor: state === "error" ? Theme.error : connected ? Theme.primary : Theme.surfaceVariantText
    readonly property bool canConnect: !busy && hasBasePassword && state !== "missing" && (!useTotp || /^[0-9]{6}$/.test(totp))

    function helperEnv() {
        return {
            "SOPHOS_VPN_CONNECTION_NAME": connectionName,
            "SOPHOS_VPN_SECRET_ACCOUNT": "connection:" + connectionName,
            "SOPHOS_VPN_OPENVPN_ONLY": "1",
            "SOPHOS_VPN_USE_TOTP": useTotp ? "1" : "0"
        }
    }

    function restoreSettings() {
        if (busy || loadingSettings) return
        loadingSettings = true
        connectionName = pluginData.connectionName || "Sophos VPN"
        const profile = (pluginData.profiles || {})[connectionName] || {}
        username = profile.username || ""
        profilePath = profile.profilePath || ""
        useTotp = profile.useTotp !== undefined ? profile.useTotp : connectionName === "Sophos VPN"
        profileChanged = false
        loadingSettings = false
        refreshStatus()
    }

    function persistSettings() {
        if (!pluginService || !pluginId) return
        const profiles = Object.assign({}, pluginData.profiles || {})
        profiles[connectionName] = {username: username, profilePath: profilePath, useTotp: useTotp}
        loadingSettings = true
        pluginService.savePluginData(pluginId, "profiles", profiles)
        pluginService.savePluginData(pluginId, "connectionName", connectionName)
        loadingSettings = false
    }

    onPluginDataChanged: restoreSettings()
    Component.onCompleted: restoreSettings()

    function refreshStatus() {
        if (busy || statusProcess.running) return
        statusProcess.environment = helperEnv()
        statusProcess.running = true
    }

    function parseStatus(raw) {
        try {
            const data = JSON.parse(raw.trim())
            connected = Boolean(data.vpn_connected !== undefined ? data.vpn_connected : data.connected)
            state = data.state || (connected ? "connected" : "disconnected")
            hasBasePassword = Boolean(data.has_base_password)
            deviceText = data.device || ""
            ip4Text = data.ip4 || ""
            if (data.username && !username) username = data.username
            if (!receivedStatus && state === "missing") selectedTab = 1
            receivedStatus = true
        } catch (_) {
            state = "error"
            messageText = "VPN-Status konnte nicht gelesen werden."
        }
    }

    function runAction(action) {
        if (busy || actionProcess.running) return
        // Discard an old status response before changing the active connection.
        if (statusProcess.running) statusProcess.running = false
        messageText = ""
        busy = true
        pendingAction = action
        actionProcess.environment = helperEnv()
        actionProcess.stdinEnabled = action === "store-password" || (action === "connect" && useTotp)
        actionProcess.command = action === "configure"
            ? ["sophos-vpn", "configure", username.trim()]
            : action === "import"
            ? ["sophos-vpn", "import", profilePath.trim(), "--name", connectionName, "--username", username.trim()]
            : ["sophos-vpn", action]
        actionProcess.running = true
    }

    function saveSettings() {
        if (busy) return
        connectionName = connectionName.trim()
        username = username.trim()
        if (!connectionName) { messageText = "Verbindungsname fehlt."; return }
        if (profileChanged && (!profilePath.trim() || !username)) {
            messageText = "Profil und Benutzername fehlen."; return
        }
        saving = true
        persistSettings()
        if (profileChanged || (state === "missing" && profilePath.trim())) runAction("import")
        else runAction("configure")
    }

    function finishSave() {
        saving = false
        replacePassword = false
        selectedTab = 0
        totp = ""
        refreshStatus()
    }

    function actionFinished(exitCode, raw) {
        const action = pendingAction
        busy = false
        pendingAction = ""
        let data
        try { data = JSON.parse(raw.trim()) } catch (_) { data = {ok: false} }
        if (exitCode !== 0 || data.ok === false) {
            saving = false
            basePassword = ""
            totp = ""
            messageText = data.message || "VPN-Aktion fehlgeschlagen."
            refreshStatus()
            return
        }
        if (action === "import" || action === "configure") {
            profileChanged = false
            if (basePassword) { runAction("store-password"); return }
        }
        if (action === "store-password") {
            basePassword = ""
            hasBasePassword = true
        }
        if (action === "connect" || action === "disconnect") {
            totp = ""
            connected = action === "connect"
            state = connected ? "connected" : "disconnected"
        }
        if (saving) finishSave()
        else refreshStatus()
    }

    Timer { interval: 15000; repeat: true; running: true; onTriggered: root.refreshStatus() }

    FileDialog {
        id: profileDialog
        title: "OpenVPN-Profil"
        nameFilters: ["OpenVPN (*.ovpn)", "Alle Dateien (*)"]
        onAccepted: {
            root.profilePath = decodeURIComponent(selectedFile.toString().replace(/^file:\/\//, ""))
            root.profileChanged = true
        }
    }

    Process {
        id: statusProcess
        command: ["sophos-vpn", "status"]
        stdout: StdioCollector { id: statusOutput }
        onExited: (exitCode) => {
            if (root.busy) return
            if (exitCode === 0) root.parseStatus(statusOutput.text)
            else { root.state = "error"; root.messageText = "VPN-Status konnte nicht gelesen werden." }
        }
    }

    Process {
        id: actionProcess
        stdout: StdioCollector { id: actionOutput }
        onStarted: {
            if (root.pendingAction === "store-password") write(root.basePassword + "\n")
            else if (root.pendingAction === "connect" && root.useTotp) write(root.totp + "\n")
        }
        onExited: (exitCode) => root.actionFinished(exitCode, actionOutput.text)
    }

    component Field: Column {
        id: control
        property alias text: input.text
        property alias placeholderText: input.placeholderText
        property alias echoMode: input.echoMode
        property string label: ""
        signal edited(string value)
        spacing: 6
        StyledText {
            width: parent.width
            visible: control.label.length > 0
            text: control.label
            color: Theme.surfaceVariantText
            font.pixelSize: Theme.fontSizeSmall
            wrapMode: Text.NoWrap; elide: Text.ElideRight
        }
        TextField {
            id: input
            width: parent.width
            height: 38
            enabled: !root.busy
            leftPadding: 10
            rightPadding: 10
            color: Theme.surfaceText
            placeholderTextColor: Theme.surfaceVariantText
            selectionColor: Theme.primary
            selectedTextColor: Theme.onPrimary
            font.pixelSize: Theme.fontSizeSmall
            onTextEdited: control.edited(text)
            background: Rectangle {
                radius: 8
                color: Theme.surfaceContainerHigh
                border.width: 1
                border.color: input.activeFocus ? Theme.primary : Theme.outlineVariant
            }
        }
    }

    component Action: Button {
        id: control
        property bool secondary: false
        implicitHeight: 40
        implicitWidth: 80
        enabled: !root.busy
        contentItem: StyledText {
            text: control.text
            font.pixelSize: Theme.fontSizeSmall
            font.weight: Font.Medium
            color: control.secondary || !control.enabled ? Theme.surfaceVariantText : Theme.onPrimary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.NoWrap; elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 8
            color: control.secondary ? "transparent" : control.enabled ? Theme.primary : Theme.surfaceContainerHigh
            border.width: control.secondary ? 1 : 0
            border.color: Theme.outlineVariant
        }
    }

    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingXS
            DankIcon { name: root.connected ? "vpn_lock" : "vpn_key"; size: root.iconSize; color: root.statusColor; anchors.verticalCenter: parent.verticalCenter }
            StyledText { text: "OpenVPN"; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceText; anchors.verticalCenter: parent.verticalCenter }
        }
    }
    verticalBarPill: Component {
        DankIcon { name: root.connected ? "vpn_lock" : "vpn_key"; size: root.iconSize; color: root.statusColor }
    }

    popoutContent: Component {
        PopoutComponent {
            Column {
                width: parent.width
                spacing: 14

                RowLayout {
                    width: parent.width
                    spacing: 20
                    Repeater {
                        model: ["Verbindung", "Einrichtung"]
                        delegate: Item {
                            required property int index
                            required property string modelData
                            Layout.preferredWidth: tabLabel.implicitWidth
                            Layout.preferredHeight: 34
                            StyledText {
                                id: tabLabel
                                text: modelData
                                font.pixelSize: Theme.fontSizeSmall
                                font.weight: Font.Medium
                                color: root.selectedTab === index ? Theme.primary : Theme.surfaceVariantText
                            }
                            Rectangle { width: parent.width; height: 2; anchors.bottom: parent.bottom; color: Theme.primary; visible: root.selectedTab === index }
                            MouseArea { anchors.fill: parent; enabled: !root.busy; cursorShape: Qt.PointingHandCursor; onClicked: root.selectedTab = index }
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                Column {
                    width: parent.width
                    spacing: 14
                    visible: root.selectedTab === 0

                    StyledText {
                        width: parent.width
                        text: root.statusText
                        color: Theme.surfaceText
                        font.pixelSize: Theme.fontSizeLarge
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }

                    Rectangle {
                        width: parent.width
                        height: profileSummary.implicitHeight + 20
                        color: "transparent"
                        radius: 9
                        border.color: Theme.outlineVariant
                        Column {
                            id: profileSummary
                            x: 10; y: 10; width: parent.width - 20
                            spacing: 3
                            StyledText { width: parent.width; text: root.connectionName; font.pixelSize: Theme.fontSizeSmall; font.weight: Font.Medium; color: Theme.surfaceText; wrapMode: Text.NoWrap; elide: Text.ElideRight }
                            StyledText { width: parent.width; text: root.username; visible: text.length > 0; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceVariantText; wrapMode: Text.NoWrap; elide: Text.ElideRight }
                        }
                    }

                    Column {
                        width: parent.width
                        spacing: 6
                        visible: !root.connected && root.useTotp
                        StyledText { width: parent.width; text: "Einmalcode (TOTP)"; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceVariantText; wrapMode: Text.NoWrap; elide: Text.ElideRight }
                        TextField {
                            id: otpInput
                            width: parent.width; height: 44
                            enabled: !root.busy
                            text: root.totp
                            onTextEdited: root.totp = text
                            placeholderText: "000000"
                            maximumLength: 6
                            validator: RegularExpressionValidator { regularExpression: /[0-9]{0,6}/ }
                            inputMethodHints: Qt.ImhDigitsOnly
                            horizontalAlignment: Text.AlignHCenter
                            font.pixelSize: Theme.fontSizeMedium
                            font.letterSpacing: 5
                            color: Theme.surfaceText
                            placeholderTextColor: Theme.surfaceVariantText
                            selectionColor: Theme.primary
                            selectedTextColor: Theme.onPrimary
                            onAccepted: { if (root.canConnect) root.runAction("connect") }
                            background: Rectangle { radius: 8; color: Theme.surfaceContainerHigh; border.color: otpInput.activeFocus ? Theme.primary : Theme.outlineVariant }
                        }
                    }

                    Action {
                        width: parent.width
                        text: root.connected ? "Verbindung trennen" : "Verbinden"
                        secondary: root.connected
                        enabled: root.connected ? !root.busy : root.canConnect
                        onClicked: root.runAction(root.connected ? "disconnect" : "connect")
                    }

                    Column {
                        width: parent.width
                        spacing: 10
                        Rectangle { width: parent.width; height: 1; color: Theme.outlineVariant }
                        Item {
                            width: parent.width; height: 26
                            StyledText { anchors.verticalCenter: parent.verticalCenter; width: parent.width - 24; text: "Verbindungsdetails"; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceVariantText; wrapMode: Text.NoWrap; elide: Text.ElideRight }
                            DankIcon { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; size: 18; name: root.showDetails ? "expand_less" : "expand_more"; color: Theme.surfaceVariantText }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.showDetails = !root.showDetails }
                        }
                        Column {
                            visible: root.showDetails
                            width: parent.width
                            spacing: 8
                            Repeater {
                                model: [{label:"VPN-Adresse", value:root.ip4Text || "—"}, {label:"Schnittstelle", value:root.deviceText || "—"}]
                                delegate: RowLayout {
                                    required property var modelData
                                    width: parent.width
                                    spacing: 10
                                    StyledText { Layout.fillWidth: true; Layout.minimumWidth: 0; text: modelData.label; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceVariantText; wrapMode: Text.NoWrap; elide: Text.ElideRight }
                                    StyledText { Layout.preferredWidth: parent.width / 2; text: modelData.value; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceText; wrapMode: Text.WrapAnywhere; horizontalAlignment: Text.AlignRight }
                                }
                            }
                            Action { width: parent.width; text: "Aktualisieren"; secondary: true; onClicked: root.refreshStatus() }
                        }
                    }
                }

                Column {
                    width: parent.width
                    spacing: 14
                    visible: root.selectedTab === 1
                    enabled: !root.busy

                    Field { width: parent.width; label: "Verbindungsname"; text: root.connectionName; onEdited: value => { root.connectionName = value; root.hasBasePassword = false } }
                    RowLayout {
                        width: parent.width
                        spacing: 6
                        Field { Layout.fillWidth: true; Layout.minimumWidth: 0; label: "OpenVPN-Profil"; text: root.profilePath; placeholderText: "/pfad/verbindung.ovpn"; onEdited: value => { root.profilePath = value; root.profileChanged = true } }
                        Action { Layout.preferredWidth: 82; Layout.alignment: Qt.AlignBottom; implicitHeight: 38; text: "Auswählen"; secondary: true; onClicked: profileDialog.open() }
                    }
                    Field { width: parent.width; label: "Benutzername"; text: root.username; onEdited: value => root.username = value }

                    Column {
                        width: parent.width
                        spacing: 6
                        StyledText { text: "Passwort"; font.pixelSize: Theme.fontSizeSmall; color: Theme.surfaceVariantText }
                        Action { width: parent.width; visible: root.hasBasePassword && !root.replacePassword; text: "Passwort ersetzen"; secondary: true; onClicked: root.replacePassword = true }
                        Field { width: parent.width; visible: !root.hasBasePassword || root.replacePassword; text: root.basePassword; label: ""; echoMode: TextInput.Password; onEdited: value => root.basePassword = value }
                    }

                    CheckBox {
                        id: totpCheck
                        width: parent.width
                        text: "Einmalcode (TOTP)"
                        checked: root.useTotp
                        onToggled: { root.useTotp = checked; root.totp = "" }
                        spacing: 8
                        padding: 0
                        indicator: Rectangle {
                            implicitWidth: 18; implicitHeight: 18
                            y: (parent.height - height) / 2
                            radius: 3
                            color: totpCheck.checked ? Theme.primary : "transparent"
                            border.color: totpCheck.checked ? Theme.primary : Theme.outline
                            StyledText { anchors.centerIn: parent; text: "✓"; visible: totpCheck.checked; color: Theme.onPrimary; font.pixelSize: Theme.fontSizeSmall }
                        }
                        contentItem: StyledText { text: totpCheck.text; leftPadding: 26; color: Theme.surfaceVariantText; font.pixelSize: Theme.fontSizeSmall; wrapMode: Text.NoWrap; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                    }

                    Action { width: parent.width; text: root.saving ? "Speichern …" : "Einrichtung speichern"; onClicked: root.saveSettings() }
                }

                StyledText {
                    width: parent.width
                    visible: root.messageText.length > 0
                    text: root.messageText
                    wrapMode: Text.WrapAnywhere
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeSmall
                }
            }
        }
    }
}
