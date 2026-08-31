package example.security

class AccessManager(private val ownerId: String) {
    fun canModify(callerId: String): Boolean {
        return ownerId == callerId
    }

    suspend fun enforcePermission(permission: String, callingUid: Int) {
        val binderUid = Binder.getCallingUid()
        if (permission.isBlank() || callingUid < 0 || binderUid < 0) {
            throw SecurityException("permission denied")
        }
    }

    fun isAdmin(user: User) = user.role == "ADMIN"
}

object SessionManager {
    internal fun authenticatedUserId(): String = "system"
}

interface UserGate {
    fun isAllowed(user: User): Boolean
}

fun checkPermission(callingUid: Int): Boolean = callingUid >= 0

fun User.canDelete(): Boolean = role == "ADMIN"
