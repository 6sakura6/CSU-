import QtQuick

Item {
    id: icon
    property string name: "info"
    property color tint: "#007aff"
    implicitWidth: 24; implicitHeight: 24
    width: 24; height: 24
    Image {
        anchors.fill: parent
        source: bridge.iconUrl(icon.name, icon.tint)
        sourceSize.width: 96
        sourceSize.height: 96
        fillMode: Image.PreserveAspectFit
    }
}
