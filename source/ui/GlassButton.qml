import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property bool primary: false
    property bool quiet: false
    property string iconName: ""
    property bool reducedMotion: bridge.state.reducedMotion
    implicitHeight: 48
    implicitWidth: contentItem.implicitWidth + (quiet ? 20 : 32)
    leftPadding: quiet ? 10 : 16
    rightPadding: leftPadding
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    opacity: enabled ? 1 : 0.45
    scale: down ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: control.reducedMotion ? 0 : 160; easing.type: Easing.OutCubic } }
    background: Rectangle {
        radius: height / 2
        color: control.primary ? (control.down ? "#0060d5" : control.hovered ? "#1687ff" : "#007aff") :
               control.quiet ? (control.hovered ? "#1c007aff" : "transparent") :
               (control.hovered ? "#f2ffffff" : "#8affffff")
        border.width: control.visualFocus ? 2 : (control.primary || control.quiet ? 0 : 1)
        border.color: control.visualFocus ? "#007aff" : "#d0def3"
        Behavior on color { ColorAnimation { duration: control.reducedMotion ? 0 : 140 } }
    }
    contentItem: RowLayout {
        spacing: 8
        IconImage {
            visible: control.iconName !== ""
            name: control.iconName
            tint: control.primary ? "white" : control.quiet ? "#007aff" : "#516c94"
            Layout.preferredWidth: 20; Layout.preferredHeight: 20
        }
        AppText {
            text: control.text
            font.pixelSize: 16
            font.weight: Font.DemiBold
            color: control.primary ? "white" : control.quiet ? "#007aff" : "#345077"
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
        }
    }
}
