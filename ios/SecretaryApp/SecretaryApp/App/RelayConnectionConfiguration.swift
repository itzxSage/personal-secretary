import CryptoKit
import Foundation
import SecretaryClient
import Security

enum RelaySetupError: Error {
    case keyUnavailable
    case identityUnavailable
    case invalidConfiguration
}

/// Opt-in staging configuration contains references, never private keys or provider credentials.
struct RelayConnectionConfiguration: Codable {
    let origin: URL
    let conversationID: UUID
    let participantID: UUID
    let deviceID: UUID
    let signingKeyAccount: String
    let clientPKCS12Account: String
    let clientPKCS12PasswordAccount: String
    let serverCertificateSHA256: String

    static func load() throws -> Self? {
#if DEBUG && os(iOS)
        try DeveloperRelayPairing.installIfProvided()
#endif
        let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let file = directory.appendingPathComponent("Secretary/relay.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        return try JSONDecoder().decode(Self.self, from: Data(contentsOf: file))
    }

    var identity: ConversationIdentity {
        ConversationIdentity(conversationID: conversationID, participantID: participantID, deviceID: deviceID)
    }

    func client() throws -> SignedConversationRelay {
        let keyQuery: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.secretary.relay",
            kSecAttrAccount as String: signingKeyAccount,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var keyItem: CFTypeRef?
        guard SecItemCopyMatching(keyQuery as CFDictionary, &keyItem) == errSecSuccess,
            let keyData = keyItem as? Data, keyData.count == 32 else { throw RelaySetupError.keyUnavailable }
        let pkcs12 = try keychainData(account: clientPKCS12Account)
        let passwordData = try keychainData(account: clientPKCS12PasswordAccount)
        guard let password = String(data: passwordData, encoding: .utf8) else {
            throw RelaySetupError.identityUnavailable
        }
        var imported: CFArray?
        let options = [kSecImportExportPassphrase as String: password]
        guard SecPKCS12Import(pkcs12 as CFData, options as CFDictionary, &imported) == errSecSuccess,
            let items = imported as? [[String: Any]],
            let first = items.first,
            let identityItem = first[kSecImportItemIdentity as String],
            let clientCertificates = first[kSecImportItemCertChain as String] as? [SecCertificate] else {
            throw RelaySetupError.identityUnavailable
        }
        let tlsIdentity = identityItem as! SecIdentity
        guard let certificateFingerprint = Data(hex: serverCertificateSHA256),
            certificateFingerprint.count == 32 else {
            throw RelaySetupError.invalidConfiguration
        }
        return try SignedConversationRelay(
            origin: origin, deviceID: deviceID,
            signingKey: Curve25519.Signing.PrivateKey(rawRepresentation: keyData),
            transport: RelayURLSessionTransport(
                host: origin.host ?? "",
                clientIdentity: tlsIdentity,
                clientCertificates: clientCertificates,
                serverCertificateSHA256: certificateFingerprint
            )
        )
    }

    private func keychainData(account: String) throws -> Data {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.secretary.relay",
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
            let data = item as? Data else { throw RelaySetupError.identityUnavailable }
        return data
    }
}

private extension Data {
    init?(hex: String) {
        guard hex.count.isMultiple(of: 2) else { return nil }
        var data = Data(capacity: hex.count / 2)
        var index = hex.startIndex
        while index < hex.endIndex {
            let next = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        self = data
    }
}
