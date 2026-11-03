import QtQuick

Rectangle {
    property alias name: icon.name
    property alias tint: icon.tint
    width: 60; height: 60; radius: 18
    color: "#17007aff"
    IconImage { id: icon; anchors.centerIn: parent; width: 34; height: 34 }
}
