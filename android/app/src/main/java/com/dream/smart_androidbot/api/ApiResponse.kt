package com.dream.smart_androidbot.api

import org.json.JSONArray
import org.json.JSONObject

sealed class ApiResponse {
    data class Success(val data: Any) : ApiResponse()
    data class Error(val message: String) : ApiResponse()
    data class RawObject(val json: JSONObject) : ApiResponse()
    data class RawArray(val json: JSONArray) : ApiResponse()
    data class Binary(val data: ByteArray) : ApiResponse()
    data class Text(val data: String) : ApiResponse()

    fun toJson(id: Any? = null): JSONObject {
        val obj = JSONObject()
        if (id != null) obj.put("id", id)
        return when (this) {
            is Success -> {
                obj.put("status", "success")
                when (val d = data) {
                    is JSONObject -> obj.put("result", d)
                    is JSONArray  -> obj.put("result", d)
                    is String     -> obj.put("result", d)
                    is Boolean    -> obj.put("result", d)
                    is Number     -> obj.put("result", d)
                    else          -> obj.put("result", d.toString())
                }
                obj
            }
            is Error -> {
                obj.put("status", "error")
                obj.put("result", message)
                obj
            }
            is RawObject -> {
                obj.put("status", "success")
                obj.put("result", json)
                obj
            }
            is RawArray -> {
                obj.put("status", "success")
                obj.put("result", json)
                obj
            }
            is Binary -> {
                obj.put("status", "success")
                obj.put("result", android.util.Base64.encodeToString(data, android.util.Base64.NO_WRAP))
                obj
            }
            is Text -> {
                obj.put("status", "success")
                obj.put("result", data)
                obj
            }
        }
    }
}
