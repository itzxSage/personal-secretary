import SwiftUI

enum AppBrand {
    static let displayName = "LifeOS"
}

enum LifeDesign {
    static let navy = Color(red: 16 / 255, green: 42 / 255, blue: 67 / 255)
    static let paleBlue = Color(red: 220 / 255, green: 238 / 255, blue: 1)
    static let surface = Color(red: 245 / 255, green: 247 / 255, blue: 249 / 255)
    static let border = Color(red: 228 / 255, green: 232 / 255, blue: 237 / 255)
    static let secondary = Color(red: 107 / 255, green: 119 / 255, blue: 133 / 255)
    static let spacing: CGFloat = 24
    static let radius: CGFloat = 24
}

struct LifeCard<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content.padding(LifeDesign.spacing)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.white, in: RoundedRectangle(cornerRadius: LifeDesign.radius))
            .overlay(RoundedRectangle(cornerRadius: LifeDesign.radius).stroke(LifeDesign.border))
    }
}
