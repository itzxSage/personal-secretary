import Foundation
import SecretaryClient

private struct DeviceSurfaceReceipt: Codable {
    let surface: String
    let receivedAt: Date
}

enum DeviceSurfaceReceiptStore {
    static let fileName = "lifeos-surface-receipts.json"

    static func record(_ source: InvocationSource) {
        do {
            let fileURL = try receiptFileURL()
            var receipts: [DeviceSurfaceReceipt] = []
            if FileManager.default.fileExists(atPath: fileURL.path) {
                let data = try Data(contentsOf: fileURL)
                receipts = try JSONDecoder().decode([DeviceSurfaceReceipt].self, from: data)
            }
            receipts.append(DeviceSurfaceReceipt(surface: source.rawValue, receivedAt: Date()))
            let data = try JSONEncoder().encode(receipts)
            try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
        } catch {
            return
        }
    }

    private static func receiptFileURL() throws -> URL {
        let directory = try FileManager.default.url(
            for: .documentDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        return directory.appendingPathComponent(fileName)
    }
}
