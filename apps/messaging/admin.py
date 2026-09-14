from django.contrib import admin

from .models import Comment, Contact, ContactTag, Conversation, FollowCheck, Message, Tag


class ContactTagInline(admin.TabularInline):
    model = ContactTag
    extra = 0
    readonly_fields = ("added_by_run", "created_at")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = (
        "username", "external_user_id", "account", "source", "is_following",
        "comments_count", "messages_count", "last_interaction_at",
    )
    list_filter = ("source", "is_following", "is_blocked", "last_interaction_at")
    search_fields = ("username", "name", "external_user_id", "email", "account__username")
    readonly_fields = ("first_seen_at", "created_at", "updated_at")
    inlines = [ContactTagInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "color", "created_at")
    search_fields = ("name", "account__username")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = (
        "external_comment_id", "contact", "account", "automation",
        "is_from_owner", "is_hidden", "received_at",
    )
    list_filter = ("is_from_owner", "is_automated_reply", "is_hidden", "received_at")
    search_fields = ("external_comment_id", "contact__username", "text")
    readonly_fields = (
        "account", "post", "external_media_id", "contact", "external_comment_id", "parent",
        "text", "is_from_owner", "is_automated_reply", "automation", "public_replied_at",
        "private_replied_at", "received_at", "created_at", "updated_at",
    )
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "account", "mode", "unread_count", "last_message_at")
    list_filter = ("mode", "is_archived")
    search_fields = ("contact__username", "contact__external_user_id", "account__username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "direction", "sender_type", "message_type", "contact",
        "account", "status", "attempt_count",
    )
    list_filter = ("direction", "sender_type", "message_type", "status", "created_at")
    search_fields = ("external_message_id", "contact__username", "account__username")
    readonly_fields = (
        "account", "conversation", "contact", "direction", "sender_type", "message_type",
        "external_message_id", "text", "attachments", "postback_payload", "reply_to",
        "comment", "automation_run", "step", "sent_by", "attempt_count", "last_error",
        "sent_at", "delivered_at", "read_at", "deleted_at", "created_at", "updated_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False


@admin.register(FollowCheck)
class FollowCheckAdmin(admin.ModelAdmin):
    list_display = ("checked_at", "contact", "automation_run", "is_following")
    list_filter = ("is_following", "checked_at")
    search_fields = ("contact__username", "contact__external_user_id")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "checked_at"

    def has_add_permission(self, request):
        return False
