import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    implicitHeight: 46
    leftPadding: 14; rightPadding: 40
    font.family: "Microsoft YaHei UI"; font.pixelSize: 16
    background: Rectangle {
        radius: 12; color: "#f5f8fd"
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus ? "#007aff" : "#dbe5f2"
    }
    contentItem: AppText { text: control.displayText; font.pixelSize: 16; color: "#345077" }
    indicator: IconImage {
        name: "chevron"; tint: "#6782a8"; rotation: 90
        x: control.width - width - 14; y: (control.height - height) / 2
        width: 18; height: 18
    }
}
