import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: field
    property string label: ""
    property alias text: input.text
    property alias placeholderText: input.placeholderText
    property alias echoMode: input.echoMode
    property alias maximumLength: input.maximumLength
    spacing: 8
    AppText { text: field.label; font.pixelSize: 15; color: "#536a8e" }
    TextField {
        id: input
        Layout.fillWidth: true
        implicitHeight: 48
        selectByMouse: true
        color: "#152743"
        font.family: "Microsoft YaHei UI"; font.pixelSize: 17
        leftPadding: 14; rightPadding: 14
        Accessible.name: field.label
        placeholderTextColor: "#8293ac"
        background: Rectangle {
            radius: 12
            color: "#f5f8fd"
            border.width: input.activeFocus ? 2 : 1
            border.color: input.activeFocus ? "#007aff" : "#dbe5f2"
        }
    }
}
