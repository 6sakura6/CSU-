import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Effects

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    title: "平安打卡助手"
    width: Math.min(1440, Screen.desktopAvailableWidth - 80)
    height: Math.min(1024, Screen.desktopAvailableHeight - 80)
    minimumWidth: 880; minimumHeight: 680
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint
    color: "transparent"
    background: Item {}
    visible: false
    font.family: "Microsoft YaHei UI"
    property var m: bridge.state
    property bool compact: width < 1280
    property bool reducedMotion: m.reducedMotion
    property string toastMessage: ""
    property real frameRadius: visibility === Window.Maximized || visibility === Window.FullScreen ? 0 : 24
    onClosing: function(event) { event.accepted = false; bridge.closeWindow() }

    Rectangle {
        id: windowMask
        width: window.width; height: window.height
        radius: window.frameRadius
        color: "white"
        antialiasing: true
        layer.enabled: true
        visible: false
    }

    Item {
        id: windowFrame
        objectName: "roundedWindowFrame"
        anchors.fill: parent
        layer.enabled: window.frameRadius > 0
        layer.effect: MultiEffect {
            maskEnabled: true
            maskSource: windowMask
            autoPaddingEnabled: false
        }

    Image { id: wallpaper; anchors.fill: parent; source: "assets/background.png"; fillMode: Image.Stretch }

    Item {
        id: titlebar
        height: 40; width: parent.width
        Rectangle { anchors.fill: parent; color: "#20ffffff" }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#80ffffff" }
        Image {
            x: 20; width: 22; height: 22; anchors.verticalCenter: parent.verticalCenter
            source: "../assets/app.png"; sourceSize.width: 44; sourceSize.height: 44
        }
        AppText { x: 55; anchors.verticalCenter: parent.verticalCenter; text: "平安打卡助手"; font.pixelSize: 15; font.weight: Font.DemiBold }
        MouseArea {
            anchors.fill: parent; anchors.rightMargin: 146
            onPressed: window.startSystemMove()
            onDoubleClicked: window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized()
        }
        Row {
            anchors.right: parent.right; height: parent.height
            Repeater {
                model: [{name:"minimize",label:"最小化"},{name:"maximize",label:"最大化"},{name:"close",label:"关闭到托盘"}]
                delegate: Button {
                    required property var modelData
                    width: 48; height: 40
                    Accessible.name: modelData.label
                    hoverEnabled: true
                    background: Rectangle { color: parent.hovered ? (modelData.name === "close" ? "#e34b54" : "#18007aff") : "transparent" }
                    contentItem: Item {
                        IconImage { name: modelData.name; width: 18; height: 18; anchors.centerIn: parent; tint: parent.parent.hovered && modelData.name === "close" ? "white" : "#233c61" }
                    }
                    onClicked: {
                        if (modelData.name === "minimize") window.showMinimized()
                        else if (modelData.name === "maximize") window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized()
                        else bridge.closeWindow()
                    }
                }
            }
        }
    }

    Flickable {
        id: scroller
        anchors { top: titlebar.bottom; left: parent.left; right: parent.right; bottom: footer.top; margins: 24; topMargin: 14; bottomMargin: 8 }
        contentWidth: width
        contentHeight: page.height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: scroller.contentHeight > scroller.height + 1 ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
        Column {
            id: page
            width: scroller.width; spacing: 16
            opacity: 0
            Component.onCompleted: introAnimation.start()
            NumberAnimation { id: introAnimation; target: page; property: "opacity"; to: 1; duration: window.reducedMotion ? 0 : 320; easing.type: Easing.OutCubic }

            GlassPanel {
                id: hero
                width: parent.width; height: window.compact ? 306 : 256
                radius: 34; glassOpacity: 0.38; backdrop: wallpaper
                Item {
                    id: appIntro
                    x: 36; y: window.compact ? 22 : 0
                    width: window.compact ? parent.width - 72 : parent.width * 0.272
                    height: window.compact ? 70 : parent.height
                    Column {
                        anchors.verticalCenter: parent.verticalCenter; width: parent.width; spacing: 10
                        AppText {
                            text: "平安打卡助手"; font.pixelSize: window.compact ? 32 : Math.min(52, hero.width * 0.036)
                            font.weight: Font.Bold; color: "#0d2143"; width: parent.width
                        }
                        AppText {
                            text: "中南大学 · 每日平安，安心安排"
                            color: "#5b759b"; font.pixelSize: window.compact ? 15 : Math.min(20, hero.width * 0.0145)
                            width: parent.width
                        }
                    }
                }
                Rectangle {
                    visible: !window.compact
                    x: appIntro.x + appIntro.width + 20; y: 46; width: 1; height: hero.height - 92
                    color: "#c8d9f2"
                }
                Item {
                    id: currentStatus
                    x: window.compact ? 36 : appIntro.x + appIntro.width + 56
                    y: window.compact ? 110 : 46
                    width: hero.width - x - nextExecution.width - 50
                    height: window.compact ? 164 : 180
                    AppText { text: "今日状态"; color: "#6981a5"; font.pixelSize: 17 }
                    Row {
                        id: stateRow; y: 29; spacing: 16
                        AppText { text: m.headline; font.pixelSize: window.compact ? 28 : 36; font.weight: Font.Bold }
                        Rectangle {
                            visible: currentStatus.width > 460
                            anchors.verticalCenter: parent.verticalCenter
                            width: statusBadge.implicitWidth + 34; height: 34; radius: 17
                            color: m.enabled ? "#b7f4df" : "#e1e8f2"
                            AppText {
                                id: statusBadge; anchors.centerIn: parent
                                text: m.busy ? "正在执行" : m.enabled ? "● 定时已开启" : "定时未开启"
                                color: m.enabled ? "#087e60" : "#607596"; font.pixelSize: 15
                            }
                        }
                    }
                    AppText {
                        y: 80; width: parent.width; font.pixelSize: window.compact ? 15 : 17; color: "#607a9f"
                        text: m.enabled && !m.busy && !m.completed ? "已设定定时任务，将在下方时间自动执行" : m.status
                        ToolTip.visible: statusHover.hovered
                        ToolTip.text: m.status
                        HoverHandler { id: statusHover }
                    }
                    Row {
                        y: 126; spacing: 10
                        GlassButton {
                            objectName: "runButton"
                            text: m.busy ? "正在检查…" : "立即检查并打卡"
                            iconName: "play"; primary: true; enabled: m.ready && !m.busy
                            reducedMotion: window.reducedMotion
                            onClicked: bridge.runTask("manual")
                        }
                        GlassButton {
                            objectName: "checkButton"
                            text: "检查登录与定位"; iconName: "link"; enabled: m.ready && !m.busy
                            reducedMotion: window.reducedMotion
                            onClicked: bridge.runTask("check")
                        }
                        GlassButton {
                            objectName: "scheduleButton"
                            text: m.busy ? "停止操作" : m.enabled ? "暂停定时" : "开始定时"
                            iconName: m.enabled || m.busy ? "pause" : "clock"
                            primary: !m.enabled && !m.busy; enabled: m.ready
                            reducedMotion: window.reducedMotion
                            onClicked: bridge.toggleSchedule()
                        }
                    }
                }
                Item {
                    id: nextExecution
                    objectName: "nextExecution"
                    width: hero.width >= 1330 ? 350 : 214
                    anchors.right: parent.right; anchors.rightMargin: 34
                    y: window.compact ? 112 : 48; height: 172
                    Rectangle { x: -24; width: 1; height: 160; color: "#c8d9f2" }
                    AppText { text: m.nextLabel; font.pixelSize: 17; color: "#6981a5" }
                    AppText {
                        y: 28; text: m.nextTime; font.family: "Segoe UI"; font.pixelSize: 64
                        font.weight: Font.DemiBold; color: "#0d2143"
                    }
                    AppText { y: 116; text: m.nextDate; font.pixelSize: 14; color: "#6981a5" }
                    TimerRing {
                        visible: nextExecution.width > 300
                        anchors.right: parent.right; y: -2
                        width: 146; height: 146
                        busy: m.busy; completed: m.completed; reducedMotion: window.reducedMotion
                    }
                    HoverHandler { id: nextHover }
                    ToolTip.visible: nextHover.hovered
                    ToolTip.text: m.nextDetail
                }
            }

            GridLayout {
                id: configArea
                width: parent.width
                columns: window.width < 1000 ? 1 : 2
                columnSpacing: 16; rowSpacing: 16
                GlassPanel {
                    id: accountCard
                    Layout.fillWidth: true; Layout.preferredHeight: 340
                    backdrop: wallpaper
                    IconTile { x: 30; y: 28; name: "person" }
                    Column {
                        x: 112; y: 32; width: parent.width - 144; spacing: 6
                        AppText { text: "校园账户"; font.pixelSize: 22; font.weight: Font.DemiBold }
                        AppText { text: "用于完成中南大学平安打卡"; color: "#6a81a4"; font.pixelSize: 16; width: parent.width }
                    }
                    Rectangle { x: 30; y: 106; width: parent.width - 60; height: 1; color: "#dce5f1" }
                    SettingRow {
                        x: 30; y: 111; width: parent.width - 60; height: 66
                        label: "学号 / 工号"; value: m.username ? m.maskedUser : "点击设置"
                        enabled: m.canEdit || false
                        onClicked: bridge.openPanel("account")
                    }
                    SettingRow {
                        x: 30; y: 179; width: parent.width - 60; height: 60
                        label: "密码"; value: m.username ? "••••••••     修改" : "设置密码"
                        divider: false; enabled: m.canEdit || false
                        onClicked: bridge.openPanel("account")
                    }
                    Rectangle { x: 30; y: 281; width: parent.width - 60; height: 1; color: "#dce5f1" }
                    IconImage { x: 32; y: 299; name: "shield"; tint: "#6c83a5"; width: 20; height: 20 }
                    AppText { x: 62; y: 298; text: "凭据仅在本机加密保存，确保账户安全"; font.pixelSize: 15; color: "#6b82a4"; width: parent.width - 90 }
                }
                GlassPanel {
                    id: scheduleCard
                    Layout.fillWidth: true; Layout.preferredHeight: 340
                    backdrop: wallpaper
                    IconTile { x: 30; y: 28; name: "clock" }
                    Column {
                        x: 112; y: 32; width: parent.width - 144; spacing: 6
                        AppText { text: "每日定时"; font.pixelSize: 22; font.weight: Font.DemiBold }
                        AppText { text: "按设定时间自动检查与执行"; color: "#6a81a4"; font.pixelSize: 16; width: parent.width }
                    }
                    Rectangle { x: 30; y: 106; width: parent.width - 60; height: 1; color: "#dce5f1" }
                    Column {
                        x: 30; y: 110; width: parent.width - 60
                        SettingRow { width: parent.width; height: 56; label: "基准时间"; value: m.start; enabled: m.canEdit || false; onClicked: bridge.openPanel("schedule") }
                        SettingRow { width: parent.width; height: 56; label: "重试截止"; value: m.end; enabled: m.canEdit || false; onClicked: bridge.openPanel("schedule") }
                        SettingRow { width: parent.width; height: 56; label: "重试间隔"; value: m.retry + " 分钟"; enabled: m.canEdit || false; onClicked: bridge.openPanel("schedule") }
                    }
                    IconImage { x: 32; y: 299; name: "info"; tint: "#6c83a5"; width: 20; height: 20 }
                    AppText { x: 62; y: 298; text: "完成后为次日保存随机延迟，实际时间见上方"; font.pixelSize: 15; color: "#6b82a4"; width: parent.width - 90 }
                }
            }

            GlassPanel {
                id: shortcuts
                objectName: "shortcuts"
                width: parent.width; height: window.width < 1000 ? 260 : 104; backdrop: wallpaper
                GridLayout {
                    anchors.fill: parent; anchors.margins: 24
                    columns: window.width < 1000 ? 1 : 5
                    columnSpacing: 24; rowSpacing: 14
                    AbstractButton {
                        objectName: "locationShortcut"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        enabled: m.canEdit || false; hoverEnabled: true
                        Accessible.name: "定位设置"
                        background: Rectangle { radius: 16; color: parent.hovered || parent.visualFocus ? "#14007aff" : "transparent" }
                        contentItem: RowLayout {
                            spacing: window.compact ? 14 : 24
                            IconTile { name: "location"; width: 58; height: 58; Layout.alignment: Qt.AlignVCenter }
                            Column {
                                Layout.fillWidth: true; spacing: 4
                                AppText { text: "定位设置"; font.pixelSize: 20; font.weight: Font.DemiBold }
                                AppText { text: m.locationManual ? "手动输入坐标" : "电脑自动定位"; color: "#6e84a6"; font.pixelSize: 15 }
                            }
                            IconImage { name: "chevron"; tint: "#8b9cba"; width: 18; height: 18 }
                        }
                        onClicked: bridge.openPanel("location")
                    }
                    Rectangle { visible: window.width >= 1000; Layout.preferredWidth: 1; Layout.fillHeight: true; color: "#dce5f1" }
                    AbstractButton {
                        objectName: "networkShortcut"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        enabled: m.canEdit || false; hoverEnabled: true
                        Accessible.name: "校园网设置"
                        background: Rectangle { radius: 16; color: parent.hovered || parent.visualFocus ? "#14007aff" : "transparent" }
                        contentItem: RowLayout {
                            spacing: window.compact ? 14 : 24
                            IconTile { name: "wifi"; width: 58; height: 58 }
                            Column {
                                Layout.fillWidth: true; spacing: 4
                                AppText { text: "校园网"; font.pixelSize: 20; font.weight: Font.DemiBold }
                                AppText { text: m.networkEnabled ? "自动认证已开启" : "自动认证未开启"; color: "#6e84a6"; font.pixelSize: 15 }
                            }
                            IconImage { name: "chevron"; tint: "#8b9cba"; width: 18; height: 18 }
                        }
                        onClicked: bridge.openPanel("network")
                    }
                    Rectangle { visible: window.width >= 1000; Layout.preferredWidth: 1; Layout.fillHeight: true; color: "#dce5f1" }
                    RowLayout {
                        Layout.fillWidth: true; spacing: window.compact ? 14 : 24
                        IconTile { name: "settings"; width: 58; height: 58 }
                        Column {
                            Layout.fillWidth: true; spacing: 4
                            AppText { text: "开机自启"; font.pixelSize: 20; font.weight: Font.DemiBold }
                            AppText { text: "登录后在托盘运行"; color: "#6e84a6"; font.pixelSize: 15 }
                        }
                        GlassSwitch {
                            objectName: "startupSwitch"
                            checked: m.loginStart
                            enabled: m.canEdit || false
                            reducedMotion: window.reducedMotion
                            Accessible.name: "开机自启动"
                            onToggled: bridge.toggleStartup(checked)
                        }
                    }
                }
            }

            GlassPanel {
                id: logPanel
                width: parent.width
                height: Math.max(186, scroller.height - hero.height - configArea.height - shortcuts.height - 48)
                backdrop: wallpaper
                RowLayout {
                    x: 30; y: 15; width: parent.width - 60; height: 40
                    IconImage { name: "document"; width: 30; height: 30 }
                    AppText { text: "运行记录"; font.pixelSize: 21; font.weight: Font.DemiBold; Layout.leftMargin: 8; Layout.fillWidth: true }
                    GlassButton { text: "查看全部"; quiet: true; iconName: "chevron"; reducedMotion: window.reducedMotion; onClicked: bridge.openPanel("logs") }
                }
                Column {
                    x: 30; y: 60; width: parent.width - 60
                    Repeater {
                        model: (m.records || []).slice(0, 3)
                        delegate: Item {
                            required property var modelData
                            width: parent.width; height: 39
                            Rectangle { width: parent.width; height: 1; color: "#dce5f1" }
                            Rectangle { x: 4; anchors.verticalCenter: parent.verticalCenter; width: 11; height: 11; radius: 6; color: modelData.attention ? "#d78b24" : "#13b981" }
                            AppText { x: 54; anchors.verticalCenter: parent.verticalCenter; text: modelData.time; font.pixelSize: 16; color: "#59759d" }
                            AppText { x: 142; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 150; text: modelData.message; font.pixelSize: 16; color: "#4b678f" }
                        }
                    }
                    AppText { visible: m.logCount === 0; text: "程序就绪，运行记录会显示在这里。"; color: "#6b82a4"; height: 60; font.pixelSize: 15 }
                }
            }
        }
    }

    RowLayout {
        id: footer
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 32; rightMargin: 24; bottomMargin: 8 }
        height: 30; spacing: 10
        IconImage { name: "info"; tint: "#6e84a6"; width: 18; height: 18 }
        AppText { text: "关闭窗口后将在托盘继续运行"; color: "#6e84a6"; font.pixelSize: 13; Layout.fillWidth: true }
        AppText { visible: m.preview; text: "预览 · 示例数据"; color: "#6e84a6"; font.pixelSize: 12 }
        GlassButton { text: "退出程序"; quiet: true; implicitHeight: 30; reducedMotion: window.reducedMotion; onClicked: bridge.exitApp() }
    }

    // Native system resize preserves desktop behavior with the custom title bar.
    Repeater {
        model: [Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge,
                Qt.TopEdge | Qt.LeftEdge, Qt.TopEdge | Qt.RightEdge,
                Qt.BottomEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge]
        delegate: MouseArea {
            required property int modelData
            z: 20
            x: (modelData & Qt.LeftEdge) ? 0 : (modelData & Qt.RightEdge) ? window.width - 5 : 5
            y: (modelData & Qt.TopEdge) ? 0 : (modelData & Qt.BottomEdge) ? window.height - 5 : 5
            width: (modelData & (Qt.LeftEdge | Qt.RightEdge)) ? 5 : window.width - 10
            height: (modelData & (Qt.TopEdge | Qt.BottomEdge)) ? 5 : window.height - 10
            enabled: window.visibility !== Window.Maximized
            cursorShape: modelData === Qt.LeftEdge || modelData === Qt.RightEdge ? Qt.SizeHorCursor :
                         modelData === Qt.TopEdge || modelData === Qt.BottomEdge ? Qt.SizeVerCursor :
                         modelData === (Qt.TopEdge | Qt.LeftEdge) || modelData === (Qt.BottomEdge | Qt.RightEdge) ? Qt.SizeFDiagCursor : Qt.SizeBDiagCursor
            onPressed: window.startSystemResize(modelData)
        }
    }

    ModalPanel {
        id: accountDialog
        title: "校园账户"; description: "账户和密码仅使用当前 Windows 用户在本机加密保存。"
        reducedMotion: window.reducedMotion
        contentItem: ColumnLayout {
            spacing: 20
            EditField { id: accountUser; label: "学号 / 工号"; placeholderText: "请输入校园账户"; maximumLength: 128; Layout.fillWidth: true }
            EditField { id: accountPassword; label: "密码"; echoMode: TextInput.Password; placeholderText: "留空沿用已保存密码"; maximumLength: 256; Layout.fillWidth: true }
            AppText { text: "首次保存或更换账户时，需要填写密码。"; color: "#6e84a6"; font.pixelSize: 14 }
        }
        footer: ModalActions {
            Item { Layout.fillWidth: true }
            GlassButton { text: "取消"; onClicked: accountDialog.close() }
            GlassButton { text: "保存账户"; primary: true; onClicked: if (bridge.saveAccount(accountUser.text, accountPassword.text)) accountDialog.close() }
        }
        onClosed: { accountPassword.text = ""; bridge.panelClosed() }
    }

    ModalPanel {
        id: scheduleDialog
        title: "每日执行时间"; description: "使用北京时间，按学校开放时段执行。"
        reducedMotion: window.reducedMotion
        contentItem: ColumnLayout {
            spacing: 18
            RowLayout {
                spacing: 20
                EditField { id: startField; label: "基准时间"; placeholderText: "21:00"; maximumLength: 5; Layout.fillWidth: true }
                EditField { id: endField; label: "重试截止"; placeholderText: "23:25"; maximumLength: 5; Layout.fillWidth: true }
            }
            RowLayout {
                AppText { text: "重试间隔（分钟）"; color: "#536a8e"; font.pixelSize: 15; Layout.fillWidth: true }
                GlassSpinBox { id: retryField; from: 1; to: 60; Accessible.name: "重试间隔，分钟" }
            }
            AppText { text: "当天完成后，为次日生成并保存 0–60 分钟随机延迟。\n实际执行时间显示在主界面，失败重试会沿用排期。"; width: 500; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone; color: "#6e84a6"; font.pixelSize: 14; lineHeight: 1.5 }
        }
        footer: ModalActions {
            Item { Layout.fillWidth: true }
            GlassButton { text: "取消"; onClicked: scheduleDialog.close() }
            GlassButton { text: "保存时间"; primary: true; onClicked: if (bridge.saveSchedule(startField.text, endField.text, retryField.value)) scheduleDialog.close() }
        }
        onClosed: bridge.panelClosed()
    }

    ModalPanel {
        id: locationDialog
        title: "定位设置"; description: "使用本人当前所在地的真实位置。"
        reducedMotion: window.reducedMotion
        contentItem: ColumnLayout {
            spacing: 16
            RowLayout {
                RadioButton { id: deviceRadio; text: "电脑自动定位"; checked: true; font.pixelSize: 16 }
                RadioButton { id: manualRadio; text: "手动输入定位"; font.pixelSize: 16 }
            }
            EditField { id: latitudeField; label: "纬度（北纬 / 南纬）"; placeholderText: "如 28.123456 或 28°7′24″"; enabled: manualRadio.checked; Layout.fillWidth: true }
            EditField { id: longitudeField; label: "经度（东经 / 西经）"; placeholderText: "如 112.123456"; enabled: manualRadio.checked; Layout.fillWidth: true }
            AppText { text: "请使用 WGS84 / GPS 坐标；北纬、东经为正。"; color: "#6e84a6"; font.pixelSize: 14; Layout.fillWidth: true }
            AppText { id: locationPreview; text: ""; color: "#2366b4"; font.pixelSize: 14; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone }
        }
        footer: ModalActions {
            GlassButton { text: "校验并换算"; quiet: true; enabled: manualRadio.checked; onClicked: locationPreview.text = bridge.validateLocation(latitudeField.text, longitudeField.text) }
            Item { Layout.fillWidth: true }
            GlassButton { text: "取消"; onClicked: locationDialog.close() }
            GlassButton { text: "保存定位"; primary: true; onClicked: if (bridge.saveLocation(manualRadio.checked, latitudeField.text, longitudeField.text)) locationDialog.close() }
        }
        onClosed: bridge.panelClosed()
    }

    ModalPanel {
        id: networkDialog
        title: "校园网设置"; description: "校园网账户独立保存；每小时检测与打卡定时分别运行。"
        reducedMotion: window.reducedMotion
        contentItem: ColumnLayout {
            spacing: 16
            RowLayout {
                AppText { text: "每小时自动检测并按需认证"; Layout.fillWidth: true; font.pixelSize: 16 }
                GlassSwitch { id: networkToggle; reducedMotion: window.reducedMotion; Accessible.name: "校园网自动认证" }
            }
            RowLayout {
                AppText { text: "认证类型"; color: "#536a8e"; font.pixelSize: 15; Layout.fillWidth: true }
                GlassComboBox {
                    id: carrierField; model: ["无需运营商", "中国移动", "中国联通", "中国电信"]
                    displayText: currentIndex < 0 ? "请选择认证类型" : currentText
                    implicitWidth: 220; font.pixelSize: 16; Accessible.name: "校园网认证类型"
                }
            }
            EditField { id: networkUser; label: "校园网账号"; placeholderText: "独立于打卡账户"; maximumLength: 128; Layout.fillWidth: true }
            EditField { id: networkPassword; label: "校园网密码"; echoMode: TextInput.Password; placeholderText: "留空沿用同一账号的已保存密码"; maximumLength: 256; Layout.fillWidth: true }
        }
        footer: ModalActions {
            GlassButton { text: "保存并测试连接"; quiet: true; onClicked: if (bridge.saveNetwork(networkToggle.checked, networkUser.text, networkPassword.text, carrierField.currentIndex, true)) networkDialog.close() }
            Item { Layout.fillWidth: true }
            GlassButton { text: "取消"; onClicked: networkDialog.close() }
            GlassButton { text: "保存设置"; primary: true; onClicked: if (bridge.saveNetwork(networkToggle.checked, networkUser.text, networkPassword.text, carrierField.currentIndex, false)) networkDialog.close() }
        }
        onClosed: { networkPassword.text = ""; bridge.panelClosed() }
    }

    ModalPanel {
        id: logsDialog
        title: "运行记录"; description: "当前会话的最近 600 条记录，最新记录在前。"
        reducedMotion: window.reducedMotion
        width: Math.min(900, window.width - 64)
        height: Math.min(640, window.height - 80)
        contentItem: ListView {
            clip: true; model: m.records || []
            ScrollBar.vertical: ScrollBar {}
            delegate: Item {
                required property var modelData
                width: ListView.view.width; height: Math.max(54, messageText.implicitHeight + 24)
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: "#dce5f1" }
                AppText { y: 14; text: modelData.date + "  " + modelData.time; font.pixelSize: 14; color: "#6e84a6" }
                AppText { id: messageText; x: 128; y: 12; width: parent.width - 150; text: modelData.message; wrapMode: Text.Wrap; elide: Text.ElideNone; font.pixelSize: 15; color: modelData.attention ? "#a56919" : "#345077" }
            }
        }
        footer: ModalActions {
            Item { Layout.fillWidth: true }
            GlassButton { text: "关闭记录"; onClicked: logsDialog.close() }
        }
        onClosed: bridge.panelClosed()
    }

    Rectangle {
        id: toast
        z: 100
        anchors.horizontalCenter: parent.horizontalCenter
        y: window.height - height - 58
        width: Math.min(window.width - 64, toastText.implicitWidth + 48); height: 52; radius: 26
        color: "#edffffff"; border.color: "#c7d9ef"; border.width: 1
        opacity: toastTimer.running ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: window.reducedMotion ? 0 : 180 } }
        AppText { id: toastText; text: window.toastMessage; anchors.centerIn: parent; font.pixelSize: 15; color: "#345077" }
        Timer { id: toastTimer; interval: 2600 }
    }

    Connections {
        target: bridge
        function onToastRequested(message) { window.toastMessage = message; toastTimer.restart() }
        function onPanelRequested(name, data) {
            if (name === "account") {
                accountUser.text = data.username; accountPassword.text = ""; accountDialog.open()
            } else if (name === "schedule") {
                startField.text = data.start; endField.text = data.end; retryField.value = data.retry; scheduleDialog.open()
            } else if (name === "location") {
                manualRadio.checked = data.manual; deviceRadio.checked = !data.manual
                latitudeField.text = data.latitude; longitudeField.text = data.longitude; locationPreview.text = ""; locationDialog.open()
            } else if (name === "network") {
                networkToggle.checked = data.enabled; networkUser.text = data.username; networkPassword.text = ""
                carrierField.currentIndex = data.carrierIndex; networkDialog.open()
            } else if (name === "logs") logsDialog.open()
        }
    }
    }
}
