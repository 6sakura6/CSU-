import QtQuick
import QtQuick.Controls

AbstractButton {
    id: row
    property string label: ""
    property string value: ""
    property bool divider: true
    property bool showChevron: true
    implicitHeight: 58
    hoverEnabled: true
    Accessible.name: label + "，" + value
    background: Rectangle {
        radius: 10
        color: row.hovered || row.visualFocus ? "#14007aff" : "transparent"
        Rectangle { visible: row.divider; x: 0; y: parent.height - 1; width: parent.width; height: 1; color: "#dce5f1" }
    }
    contentItem: Item {
        AppText { text: row.label; anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 18 }
        AppText {
            text: row.value
            anchors.right: chevron.left; anchors.rightMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            font.pixelSize: 18
            width: Math.min(implicitWidth, parent.width * 0.49)
            horizontalAlignment: Text.AlignRight
        }
        IconImage {
            id: chevron; name: "chevron"; tint: "#8296b7"
            visible: row.showChevron
            width: row.showChevron ? 18 : 0; height: 18
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        }
    }
}
