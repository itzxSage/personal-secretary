import CryptoKit
import Foundation
import Security

enum OutboxKeyStoreError: Error {
    case read(OSStatus)
    case write(OSStatus)
}

struct OutboxKeyStore {
    static let shared = OutboxKeyStore()

    private init() {}

    private let service = "com.secretary.outbox"
    private let account = "encryption-key"

    func key() throws -> SymmetricKey {
        if let data = try load() {
            return SymmetricKey(data: data)
        }
        let newKey = SymmetricKey(size: .bits256)
        try save(newKey.withUnsafeBytes { Data($0) })
        return newKey
    }

    private func load() throws -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = item as? Data else {
            throw OutboxKeyStoreError.read(status)
        }
        return data
    }

    private func save(_ data: Data) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            kSecValueData as String: data,
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw OutboxKeyStoreError.write(status)
        }
    }
}
