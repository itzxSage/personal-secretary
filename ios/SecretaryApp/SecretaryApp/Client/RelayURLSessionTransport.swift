import CryptoKit
import Foundation
import Security

/// Real mTLS client. The staging server leaf is pinned; redirects are refused.
public final class RelayURLSessionTransport: NSObject, URLSessionDelegate, URLSessionTaskDelegate, Sendable {
    private let credential: URLCredential
    private let host: String
    private let serverCertificateSHA256: Data

    public init(
        host: String,
        clientIdentity: SecIdentity,
        clientCertificates: [SecCertificate],
        serverCertificateSHA256: Data
    ) {
        self.host = host
        self.serverCertificateSHA256 = serverCertificateSHA256
        self.credential = URLCredential(
            identity: clientIdentity,
            certificates: clientCertificates,
            persistence: .none
        )
        super.init()
    }

    public func urlSession(
        _ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }

    public func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        handle(challenge, completionHandler: completionHandler)
    }

    public func urlSession(
        _ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        handle(challenge, completionHandler: completionHandler)
    }

    private func handle(
        _ challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        let method = challenge.protectionSpace.authenticationMethod
#if DEBUG && os(iOS)
        recordDiagnostic("challenge method=\(method) host=\(challenge.protectionSpace.host)")
#endif
        if method == NSURLAuthenticationMethodClientCertificate {
            guard challenge.protectionSpace.host == host, challenge.previousFailureCount == 0 else {
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
            completionHandler(.useCredential, credential)
        } else if method == NSURLAuthenticationMethodServerTrust {
            guard challenge.protectionSpace.host == host,
                challenge.previousFailureCount == 0,
                let trust = challenge.protectionSpace.serverTrust,
                let certificates = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
                let leaf = certificates.first else {
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
            let fingerprint = Data(SHA256.hash(data: SecCertificateCopyData(leaf) as Data))
            guard fingerprint == serverCertificateSHA256 else {
#if DEBUG && os(iOS)
                recordDiagnostic(
                    "pin mismatch received=\(fingerprint.hex) expected=\(serverCertificateSHA256.hex)"
                )
#endif
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
            guard let anchor = certificates.last else {
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
            let policyStatus = SecTrustSetPolicies(trust, SecPolicyCreateSSL(true, host as CFString))
            let anchorStatus = SecTrustSetAnchorCertificates(trust, [anchor] as CFArray)
            let anchorsOnlyStatus = SecTrustSetAnchorCertificatesOnly(trust, true)
            var trustError: CFError?
            let trusted = SecTrustEvaluateWithError(trust, &trustError)
#if DEBUG && os(iOS)
            recordDiagnostic(
                "server pin matched policy=\(policyStatus) anchor=\(anchorStatus) "
                    + "anchorsOnly=\(anchorsOnlyStatus) trusted=\(trusted) "
                    + "error=\(String(describing: trustError))"
            )
#endif
            guard policyStatus == errSecSuccess, anchorStatus == errSecSuccess,
                anchorsOnlyStatus == errSecSuccess, trusted else {
                completionHandler(.cancelAuthenticationChallenge, nil)
                return
            }
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }

#if DEBUG && os(iOS)
    private func recordDiagnostic(_ value: String) {
        guard let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
        else { return }
        try? value.data(using: .utf8)?.write(
            to: directory.appendingPathComponent("lifeos-relay-transport-diagnostic.txt"),
            options: [.atomic, .completeFileProtection]
        )
    }
#endif
}

#if DEBUG && os(iOS)
private extension Data {
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}
#endif

extension RelayURLSessionTransport: RelayHTTPTransport {
    public func send(_ request: URLRequest) async throws -> RelayHTTPResponse {
        guard request.url?.scheme == "https", request.url?.host == host else {
            throw ConversationRelayError.invalidEndpoint
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 30
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw ConversationRelayError.invalidResponse
        }
        var body = Data()
        for try await byte in bytes {
            guard body.count < 2 * 1024 * 1024 else { throw ConversationRelayError.responseTooLarge }
            body.append(byte)
        }
        return RelayHTTPResponse(status: response.statusCode, body: body)
    }
}
